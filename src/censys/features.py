"""Deterministic Stage 4 feature extraction and entity-epoch materialization."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.models import (
    EntityEpochRecord,
    FeatureCatalogRecord,
    FeatureFamily,
    FeatureObservationRecord,
    FeatureStability,
    HostObservationRecord,
    ServiceObservationRecord,
)
from src.provenance import sha256_text


CANONICALIZER_VERSION = "feature-canonicalizer-v1"
_SHARED_PRODUCTS = {"apache", "httpd", "nginx", "openssh", "iis"}


@dataclass(frozen=True)
class FeatureExtractionBatch:
    features: tuple[FeatureCatalogRecord, ...]
    observations: tuple[FeatureObservationRecord, ...]


def _canonical(value: object) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).strip().lower().split())
    return normalized or None


def _feature_id(
    family: FeatureFamily, feature_type: str, value: str, extractor_version: str
) -> str:
    return "feature-" + sha256_text(
        "|".join([
            family.value, feature_type, value, CANONICALIZER_VERSION, extractor_version
        ])
    )[:20]


def extract_observation_features(
    hosts: list[HostObservationRecord],
    services: list[ServiceObservationRecord],
    *,
    extractor_version: str,
) -> FeatureExtractionBatch:
    """Extract stable/queryable and diagnostic/unavailable features with provenance."""

    host_map = {item.observation_id: item for item in hosts}
    features: dict[str, FeatureCatalogRecord] = {}
    observations: dict[str, FeatureObservationRecord] = {}

    def add(
        *,
        host: HostObservationRecord,
        family: FeatureFamily,
        feature_type: str,
        value: object,
        query_field: str | None,
        stability: FeatureStability,
        service: ServiceObservationRecord | None = None,
        shared_or_default: bool = False,
    ) -> None:
        canonical = _canonical(value)
        if canonical is None:
            return
        feature_id = _feature_id(family, feature_type, canonical, extractor_version)
        available_at = host.collected_at
        source_fingerprint_id = None
        if feature_type in {"cert_sha256", "spki_sha256", "ssh_key", "jarm", "ja4"}:
            source_fingerprint_id = "fp-" + sha256_text(
                feature_type.replace("_sha256", "").replace("ssh_key", "ssh")
                + "|" + canonical
            )[:20]
        record = FeatureCatalogRecord(
            feature_id=feature_id,
            feature_family=family,
            feature_type=feature_type,
            canonical_value=canonical,
            canonical_value_hash=sha256_text(canonical),
            query_field=query_field,
            canonicalizer_version=CANONICALIZER_VERSION,
            extractor_version=extractor_version,
            first_available_at=available_at,
            stability=stability,
            shared_or_default=shared_or_default,
            source_fingerprint_id=source_fingerprint_id,
        )
        existing = features.get(feature_id)
        if existing and available_at < existing.first_available_at:
            record = existing.model_copy(update={"first_available_at": available_at})
        elif existing:
            record = existing
        features[feature_id] = record
        service_id = service.service_observation_id if service else None
        material = "|".join([feature_id, host.observation_id, service_id or "host"])
        observation = FeatureObservationRecord(
            feature_observation_id="feature-observation-" + sha256_text(material)[:20],
            feature_id=feature_id,
            observation_id=host.observation_id,
            service_observation_id=service_id,
            query_run_id=host.query_run_id,
            observed_at=(service.observed_at if service and service.observed_at else host.observed_at),
            available_at=available_at,
        )
        observations[observation.feature_observation_id] = observation

    for host in hosts:
        if not host.host_observed:
            continue
        add(host=host, family=FeatureFamily.NETWORK, feature_type="asn", value=host.asn,
            query_field="host.autonomous_system.asn", stability=FeatureStability.STABLE)
        add(host=host, family=FeatureFamily.NETWORK, feature_type="prefix", value=host.prefix,
            query_field="host.autonomous_system.bgp_prefix", stability=FeatureStability.STABLE)
    for service in services:
        host = host_map.get(service.observation_id)
        if host is None:
            raise ValueError("feature service has no host observation")
        if not host.host_observed:
            continue
        specifications = (
            (FeatureFamily.IDENTITY, "cert_sha256", service.cert_sha256,
             "host.services.tls.certificates.leaf_data.fingerprint", FeatureStability.STABLE, False),
            (FeatureFamily.IDENTITY, "spki_sha256", service.spki_sha256,
             "host.services.tls.certificates.leaf_data.spki_subject_fingerprint", FeatureStability.STABLE, False),
            (FeatureFamily.IDENTITY, "ssh_key", service.ssh_key_hash,
             "host.services.ssh.server_host_key.fingerprint_sha256", FeatureStability.STABLE, False),
            (FeatureFamily.TLS, "jarm", service.jarm,
             "host.services.jarm.fingerprint", FeatureStability.STABLE,
             bool(service.jarm and set(str(service.jarm)) <= {"0"})),
            (FeatureFamily.TLS, "ja4", service.ja4,
             "host.services.tls.ja4", FeatureStability.STABLE, False),
            (FeatureFamily.HTTP, "banner_hash", service.banner_hash,
             None, FeatureStability.UNAVAILABLE, False),
            (FeatureFamily.HTTP, "title_hash", service.http_title_hash,
             None, FeatureStability.UNAVAILABLE, False),
            (FeatureFamily.SERVICE, "port", service.port,
             "host.services.port", FeatureStability.STABLE, False),
            (FeatureFamily.SERVICE, "protocol", service.protocol,
             "host.services.service_name", FeatureStability.STABLE, False),
            (FeatureFamily.DEVICE, "software_vendor", service.software_vendor,
             "host.services.software.vendor", FeatureStability.STABLE, False),
            (FeatureFamily.DEVICE, "software_product", service.software_product,
             "host.services.software.product", FeatureStability.STABLE,
             _canonical(service.software_product) in _SHARED_PRODUCTS),
            (FeatureFamily.DEVICE, "software_version", service.software_version,
             "host.services.software.version", FeatureStability.UNSTABLE, False),
        )
        for family, feature_type, value, query_field, stability, shared in specifications:
            add(host=host, family=family, feature_type=feature_type, value=value,
                query_field=query_field, stability=stability, service=service,
                shared_or_default=shared)
    return FeatureExtractionBatch(
        tuple(sorted(features.values(), key=lambda item: item.feature_id)),
        tuple(sorted(observations.values(), key=lambda item: item.feature_observation_id)),
    )


def build_entity_epochs(
    hosts: list[HostObservationRecord],
    feature_observations: list[FeatureObservationRecord],
    features: list[FeatureCatalogRecord],
    *,
    resolution_version: str,
) -> tuple[EntityEpochRecord, ...]:
    """Split an indicator epoch only on disjoint strong identity evidence."""

    identity_ids = {
        item.feature_id for item in features
        if item.feature_family is FeatureFamily.IDENTITY
        and item.stability is FeatureStability.STABLE
    }
    by_observation: dict[str, set[str]] = {}
    for item in feature_observations:
        if item.feature_id in identity_ids:
            by_observation.setdefault(item.observation_id, set()).add(item.feature_id)
    grouped: dict[str, list[HostObservationRecord]] = {}
    for host in hosts:
        if host.host_observed:
            grouped.setdefault(host.indicator_id, []).append(host)
    epochs: list[EntityEpochRecord] = []
    for indicator_id, records in sorted(grouped.items()):
        ordered = sorted(records, key=lambda item: (
            item.observed_at or item.collected_at, item.collected_at, item.observation_id
        ))
        chunks: list[list[HostObservationRecord]] = []
        chunk_identities: list[set[str]] = []
        for host in ordered:
            identities = by_observation.get(host.observation_id, set())
            if chunks and identities and chunk_identities[-1] and identities.isdisjoint(chunk_identities[-1]):
                chunks.append([host])
                chunk_identities.append(set(identities))
            else:
                if not chunks:
                    chunks.append([])
                    chunk_identities.append(set())
                chunks[-1].append(host)
                chunk_identities[-1].update(identities)
        for chunk, identities in zip(chunks, chunk_identities):
            start = chunk[0].observed_at or chunk[0].collected_at
            end = chunk[-1].observed_at or chunk[-1].collected_at
            observation_ids = [item.observation_id for item in chunk]
            material = "|".join([indicator_id, resolution_version, *observation_ids])
            epochs.append(EntityEpochRecord(
                entity_epoch_id="entity-epoch-" + sha256_text(material)[:20],
                indicator_id=indicator_id,
                valid_from=start,
                valid_to=end,
                observation_ids=observation_ids,
                identity_feature_ids=sorted(identities),
                resolution_version=resolution_version,
                available_at=max(item.collected_at for item in chunk),
            ))
    return tuple(epochs)
