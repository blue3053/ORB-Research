"""Censys Q0∼Q3 query 통제 계층.

목적: query 생성과 live collection을 분리하고 version·freeze·cutoff를 강제한다.
지원 RQ: RQ1∼RQ5.
보안: live API는 이 package가 직접 호출하지 않으며 원본 gate를 유지한다.
"""

