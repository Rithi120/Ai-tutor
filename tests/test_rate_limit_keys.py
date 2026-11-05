"""Rate limits count per signed-in account, not per client address (final docs review).

Behind a proxy, a school network or a family router every student shares one address. With
50 of the limiter rules keyed per address, one class answering questions together ran into
/api/answer's 30 a minute, and the tenth project created anywhere on the school network
refused everyone else's for the hour. Rules on routes that need a login are keyed by
`user_rate_key` (the account; the address for an anonymous request, which login_required
then refuses). Anonymous routes - register, login, the contact form - stay per address,
because there is no account to key them by. No limit's number changed.
"""

import re
import unittest
from pathlib import Path

import app as application
from learnova.extensions import db
from tests.config_isolation import keep_attribute, keep_config

ROOT = Path(application.__file__).resolve().parent
PASSWORD = "correct-horse-battery"
# The only rules that may stay per address: nobody is signed in when they are used.
ANONYMOUS_ROUTES = {
    '@app.route("/register", methods=["GET", "POST"])',
    '@app.route("/login", methods=["GET", "POST"])',
    '@app.post("/contact")',
}


def limiter_rules(source: str) -> list[tuple[str, str, list[str]]]:
    """(route decorator, limiter line, the decorators below it) for every limiter rule."""

    lines = source.split("\n")
    rules = []
    for index, line in enumerate(lines):
        if not re.match(r"@limiter\.(limit|shared_limit)\(", line):
            continue
        route = next(lines[j] for j in range(index - 1, max(0, index - 6), -1) if lines[j].startswith("@app."))
        below = []
        for j in range(index + 1, index + 8):
            if lines[j].startswith("def "):
                break
            below.append(lines[j].strip())
        rules.append((route, line, below))
    return rules


class PerAccountLimitTests(unittest.TestCase):
    def setUp(self):
        config = application.app.config
        keep_config(self, config, "TESTING", "WTF_CSRF_ENABLED", "SUBSCRIPTION_QUOTAS_ENFORCED")
        keep_attribute(self, application.limiter, "enabled")
        self.addCleanup(application.limiter.reset)
        config.update(TESTING=True, WTF_CSRF_ENABLED=False, SUBSCRIPTION_QUOTAS_ENFORCED=False)
        application.SESSIONS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        # Two students on one school network: the test client sends 127.0.0.1 for both.
        self.lea = self.register("lea")
        self.max = self.register("max")

    def register(self, name):
        client = application.app.test_client()
        response = client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                                  "password": PASSWORD, "grade": "9"})
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:300])
        return client

    def limits_on(self):
        application.app.config.update(TESTING=False)       # the request filter lets limits run
        application.limiter.enabled = True
        application.limiter.reset()

    def test_answers_count_per_student_not_per_school_network(self):
        self.limits_on()
        lea = [self.lea.post("/api/answer", json={}).status_code for _ in range(31)]
        max_first = self.max.post("/api/answer", json={}).status_code
        self.assertNotIn(429, lea[:30], "30 a minute for Lea")
        self.assertEqual(lea[30], 429, "the 31st answer in the minute is refused")
        self.assertNotEqual(max_first, 429, "Max on the same address has his own bucket")

    def test_project_creation_counts_per_student(self):
        self.limits_on()
        # JSON, so a refusal is a 429 (a form post gets a flash and a redirect instead).
        lea = [self.lea.post("/projects", json={}).status_code for _ in range(11)]
        max_first = self.max.post("/projects", json={}).status_code
        self.assertNotIn(429, lea[:10], "10 an hour for Lea")
        self.assertEqual(lea[10], 429)
        self.assertNotEqual(max_first, 429, "the school's other students can still start projects")

    def test_an_anonymous_request_falls_back_to_the_address(self):
        anonymous = application.app.test_client()
        neighbour = application.app.test_client()
        elsewhere = application.app.test_client()
        self.limits_on()
        refused = [anonymous.post("/api/answer", json={}).status_code for _ in range(31)]
        self.assertEqual(set(refused[:30]), {401}, "login_required still refuses them")
        self.assertEqual(refused[30], 429, "anonymous requests share their address's bucket")
        self.assertEqual(neighbour.post("/api/answer", json={}).status_code, 429, "same address, same bucket")
        self.assertEqual(elsewhere.post("/api/answer", json={},
                                        environ_base={"REMOTE_ADDR": "203.0.113.9"}).status_code, 401)

    def test_login_stays_per_address(self):
        first = application.app.test_client()
        second = application.app.test_client()
        elsewhere = application.app.test_client()
        self.limits_on()
        wrong = {"identifier": "lea", "password": "wrong"}       # JSON: a refusal is a 429, not a redirect
        attempts = [first.post("/login", json=wrong).status_code for _ in range(21)]
        self.assertNotIn(429, attempts[:20])
        self.assertEqual(attempts[20], 429, "20 a minute per address")
        self.assertEqual(second.post("/login", json=wrong).status_code, 429,
                         "another browser on the same address shares the login bucket")
        self.assertNotEqual(elsewhere.post("/login", json=wrong,
                                           environ_base={"REMOTE_ADDR": "203.0.113.9"}).status_code, 429)


class EveryRuleIsKeyedTests(unittest.TestCase):
    def test_every_login_required_rule_uses_the_account_key(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        rules = limiter_rules(source)
        self.assertGreaterEqual(len(rules), 60, "the scan found the limiter rules")
        per_address = []
        for route, line, below in rules:
            needs_login = any(item in {"@login_required", "@_premium_admin_required"} for item in below)
            with self.subTest(route=route):
                if needs_login:
                    self.assertIn("key_func=user_rate_key", line)
                elif "key_func=" not in line:
                    per_address.append(route)
        self.assertEqual(set(per_address), ANONYMOUS_ROUTES)
        self.assertEqual(len(per_address), len(ANONYMOUS_ROUTES))

    def test_the_named_routes_from_the_review(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        for route in ('@app.post("/api/answer")', '@app.route("/projects", methods=["GET", "POST"])',
                      '@app.post("/projects/<int:project_id>/quick-start")',
                      '@app.post("/projects/<int:project_id>/pages/add")',
                      '@app.post("/projects/<int:project_id>/reanalyze")'):
            with self.subTest(route=route):
                limit_line = source.split(route + "\n", 1)[1].split("\n", 1)[0]
                self.assertTrue(limit_line.startswith("@limiter.limit("), limit_line)
                self.assertIn("key_func=user_rate_key", limit_line)

    def test_the_account_key_falls_back_to_the_address(self):
        with application.app.test_request_context("/", environ_base={"REMOTE_ADDR": "198.51.100.7"}):
            self.assertEqual(application.user_rate_key(), "198.51.100.7")


if __name__ == "__main__":
    unittest.main()
