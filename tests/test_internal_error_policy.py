"""내부화한 ORB Censys 오류 분류·재시도 정책 테스트.

목적: SSL은 fail-fast, rate limit·timeout·5xx는 retryable로 유지되는지 검증한다.
지원 RQ: RQ1∼RQ5 Censys completeness와 실패율 보고.
설계: network 없이 합성 예외 메시지만 사용한다.
"""
from __future__ import annotations

import unittest

from src.reused.orbhunt_v5.error_policy import (
    classify_censys_exception, is_retryable_censys_error,
)


class InternalErrorPolicyTests(unittest.TestCase):
    def test_ssl_is_fail_fast(self):
        category, status = classify_censys_exception(Exception("certificate verify failed"))
        self.assertEqual("ssl_cert_verify_failed", category)
        self.assertFalse(is_retryable_censys_error(category, status))

    def test_rate_limit_and_server_errors_are_retryable(self):
        for message in ("Status 429", "status code: 503", "Status 504"):
            category, status = classify_censys_exception(Exception(message))
            self.assertTrue(is_retryable_censys_error(category, status))


if __name__ == "__main__":
    unittest.main()
