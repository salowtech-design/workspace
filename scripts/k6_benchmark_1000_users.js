import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

// Custom metrics for Salow Workspace 1,000-User Soak
export const loginDuration = new Trend('login_duration');
export const navigationDuration = new Trend('navigation_duration');
export const writeDuration = new Trend('write_duration');
export const rateLimitThrottles = new Counter('rate_limit_throttles_429');
export const authSuccessRate = new Rate('auth_success_rate');

export const options = {
  scenarios: {
    steady_soak_workload: {
      executor: 'ramping-vus',
      startVUs: 10,
      stages: [
        { duration: '5m', target: 250 },   // Ramp up to 250
        { duration: '10m', target: 500 },  // Ramp to 500
        { duration: '15m', target: 1000 }, // Full 1,000 active users
        { duration: '90m', target: 1000 }, // Steady soak for 90 minutes
        { duration: '5m', target: 0 },     // Ramp down
      ],
      gracefulRampDown: '30s',
    },
  },
  thresholds: {
    'navigation_duration': ['p(95)<500', 'p(99)<1500'], // Read SLO
    'write_duration': ['p(95)<800'],                     // Write SLO
    'auth_success_rate': ['rate>0.99'],                  // > 99% success
    'http_req_failed': ['rate<0.01'],                    // Error rate < 1%
  },
};

const BASE_URL = __ENV.TARGET_URL || 'http://localhost:8000';

export default function () {
  const userIndex = (__VU % 1000) + 1;
  const employeeId = `ST-BENCH-${String(userIndex).padStart(4, '0')}`;
  const password = 'BenchmarkUserPass2026!';

  // Stage 1: Real HTTP Authentication
  const loginRes = http.post(`${BASE_URL}/auth/login/`, {
    employee_id: employeeId,
    password: password,
  }, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    redirects: 0,
  });

  loginDuration.add(loginRes.timings.duration);
  const isAuthRedirect = loginRes.status === 302;
  authSuccessRate.add(isAuthRedirect);

  if (loginRes.status === 429) {
    rateLimitThrottles.add(1);
  }

  // Stage 2: Authenticated Dashboard Read
  const dashRes = http.get(`${BASE_URL}/auth/security/`, {
    headers: { 'Accept': 'text/html' },
  });
  navigationDuration.add(dashRes.timings.duration);
  check(dashRes, {
    'dashboard status is 200': (r) => r.status === 200,
  });

  // Stage 3: Authenticated State Save (Write)
  const writeRes = http.post(`${BASE_URL}/auth/security/draft/save/`, {
    section: 'workplace_safety',
    draft_content: JSON.stringify({ notes: 'k6 load test verified write' }),
  }, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  });
  writeDuration.add(writeRes.timings.duration);
  check(writeRes, {
    'write status is 200': (r) => r.status === 200,
  });

  // Realistic human think time
  sleep(Math.random() * 2 + 1);
}
