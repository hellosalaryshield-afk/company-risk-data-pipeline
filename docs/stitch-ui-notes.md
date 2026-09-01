# Stitch UI Notes

The user shared Stitch screen IDs:

- Design System: `asset-stub-assets_df32c29696884f8ea84b05249d5a5247`
- Risk Dashboard - TCS: `d2ac0b33a2214f7b92426a9c705e9e7f`
- Company Registry & Entity Resolution: `b34c755338544ffd8f4b3d079067a67a`
- Data Sources & Pipeline Health: `22283068faa44a459e403e760cdd94bc`

No hosted download URLs were included in the message. To import Stitch assets/code, we need the actual export links or files. Screen IDs alone are not enough for `curl -L`.

## Backend Endpoints These Screens Need

### Company Registry & Entity Resolution

- `GET /companies`
- `POST /companies`
- `POST /companies/resolve`

### Data Sources & Pipeline Health

- `GET /sources`
- `GET /collection-runs`
- `POST /collections`

### Risk Dashboard

- `GET /companies/{id}`
- `GET /companies/{id}/kpis`
- `GET /companies/{id}/latest-report`

## UI Recommendation

Build a simple internal web UI after the API and source pipeline work. The UI should be for testing and demonstration first, not a polished public dashboard.

First useful screens:

1. Company resolver tester.
2. Company registry table.
3. Source test runner.
4. Latest company report preview.
