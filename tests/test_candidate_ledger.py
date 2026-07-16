"""Stage 6 prospective time and canonical candidate tests."""
from datetime import datetime, timedelta, timezone
import unittest

from src.models import (
    HostObservationRecord, ObservationTimeBasis, ProspectiveTimeStatus,
    QueryFreezeManifestRecord, ServiceObservationRecord, Transport,
)
from src.prospective.candidate_ledger import (
    build_candidate, build_observation_event, classify_observation_time,
)


class CandidateLedgerTests(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
        self.freeze = QueryFreezeManifestRecord(
            freeze_manifest_id="freeze", query_id="qry", design_id="design",
            query_hash="a" * 64, source_manifest_hash="b" * 64,
            query_cutoff_at=self.t0, schedule_id="schedule", review_id="review",
            frozen_at=self.t0 + timedelta(days=1),
            valid_for_test_from=self.t0 + timedelta(days=2),
        )

    def _host(self, observed_at):
        return HostObservationRecord(
            observation_id="obs", indicator_id="ioc", observed_at=observed_at,
            collected_at=self.t0 + timedelta(days=3),
            observation_time_basis=(ObservationTimeBasis.UNAVAILABLE if observed_at is None
                                    else ObservationTimeBasis.CENSYS_HOST_UPDATED_AT),
            host_observed=True, raw_record_hash="c" * 64, query_run_id="run",
        )

    def test_pre_freeze_record_is_not_prospective(self):
        self.assertEqual(ProspectiveTimeStatus.PRE_FREEZE, classify_observation_time(
            self.t0 + timedelta(days=1), self.freeze.valid_for_test_from
        ))

    def test_missing_observed_time_is_preserved_as_unresolved(self):
        event = build_observation_event(
            self._host(None), opportunity_id="opp", entity_epoch_id="epoch",
            freeze=self.freeze, recorded_at=self.t0 + timedelta(days=3),
        )
        self.assertEqual(ProspectiveTimeStatus.UNRESOLVED, event.time_status)
        with self.assertRaisesRegex(ValueError, "eligible"):
            build_candidate(event, query_id="qry", query_version="v1", query_hash="a" * 64,
                            discovery_feature_ids=[], discovery_source_family_ids=[])

    def test_duplicate_rerun_has_same_candidate_identity(self):
        event = build_observation_event(
            self._host(self.t0 + timedelta(days=2)), opportunity_id="opp",
            entity_epoch_id="epoch", freeze=self.freeze,
            recorded_at=self.t0 + timedelta(days=3),
        )
        kwargs = dict(query_id="qry", query_version="v1", query_hash="a" * 64,
                      discovery_feature_ids=["f1"], discovery_source_family_ids=["family-a"])
        self.assertEqual(build_candidate(event, **kwargs), build_candidate(event, **kwargs))

    def test_service_level_pre_freeze_time_blocks_host_level_eligibility(self):
        host = self._host(self.t0 + timedelta(days=3))
        service = ServiceObservationRecord(
            service_observation_id="svc", observation_id=host.observation_id,
            port=443, transport=Transport.TCP, protocol="https",
            observed_at=self.t0 + timedelta(days=1),
            observation_time_basis=ObservationTimeBasis.CENSYS_SERVICE_OBSERVED_AT,
            extractor_version="fixture",
        )
        event = build_observation_event(
            host, opportunity_id="opp", entity_epoch_id="epoch", freeze=self.freeze,
            recorded_at=self.t0 + timedelta(days=3), services=[service],
        )
        self.assertEqual(ProspectiveTimeStatus.PRE_FREEZE, event.time_status)
        self.assertEqual(ProspectiveTimeStatus.PRE_FREEZE,
                         event.record_time_statuses[service.service_observation_id])


if __name__ == "__main__":
    unittest.main()
