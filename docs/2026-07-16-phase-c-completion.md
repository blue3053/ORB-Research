# Phase C 완료 보고서

## 결과

Phase C Stage 4의 feature catalog, entity epoch, matched reference/background, feature statistics와 사람 검토 기반 query eligibility를 구현했다. 모든 query-eligible feature는 anchor 분자·분모, background 분자·분모, cutoff, source query run, raw observation과 eligibility review로 역추적할 수 있다.

## 구현 범위

- cert/SPKI, SSH key, JARM/JA4, HTTP hash, port/protocol, device/software, ASN/prefix feature를 deterministic ID로 materialize한다.
- extractor와 canonicalizer version, source query run, host/service observation, observed/available time을 보존한다.
- HTTP hash처럼 안정적인 Censys query field가 없는 feature는 `unavailable`, 정확한 software version은 `unstable`로 보존하고 승격을 차단한다.
- raw observation과 entity epoch를 분리하며 강한 identity evidence가 유지되면 merge하고 완전히 교체되면 split한다.
- reference set은 protocol, ports, product, time window strata와 source execution manifest를 고정한다.
- `global_rarity` 주장을 허용하지 않고 matched-background prevalence와 reference lift만 계산한다.
- anchor/background numerator와 denominator, Wilson uncertainty interval, cutoff, source manifest를 불변 snapshot으로 저장한다.
- cutoff 이후 feature, shared/default, unstable/unavailable, 낮은 anchor support, background 과다·누락을 fail-closed 처리한다.
- 자동 평가는 `candidate`까지만 만들며 accepted human review 이후에만 `eligible_feature_ids()`에 노출한다.
- `feature-build`, `feature-build-background`, `feature-assess`, `feature-review`, `cti-audit-phase-c` CLI를 추가했다.

## 검증

- Phase C 집중 테스트에서 deterministic ID, 반복 관측, epoch split/merge, cutoff, matched denominator, source manifest 재계산, 통계 변조 차단, shared/default 차단, 사람 검토 게이트를 검증했다.
- 전체 오프라인 회귀 테스트 89개가 통과했다.
- Python compile 검사와 CLI parser 로딩이 통과했다.
- 실제 Censys 네트워크 호출과 운영 데이터베이스 변경은 수행하지 않았다.

## 라우팅과 독립 검토

라우팅은 `lead=Sol/high; builder=Terra/high; reviewer=Sol/xhigh` 기준을 적용했다. 독립 검토 관점에서 reference source-run manifest, anchor source decision과 raw run의 대응, time-window stratum, 동일 feature 후속 관측의 first-availability 보존을 재검사하고 보완했다.

## 다음 increment

다음 단계는 Phase D Stage 5다. accepted CTI clause와 Phase C의 reviewed eligible feature를 조합하는 query composer, clause provenance, bounded development precheck, human query review, immutable freeze와 schedule/budget 고정을 구현해야 한다.
