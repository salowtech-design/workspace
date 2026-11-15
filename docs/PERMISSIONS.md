# Role & Permission Matrix

*Policy: Default Deny. Server-side validation on every route.*

## Defined Roles

- `super_admin`: Platform configuration, emergency administration, unrestricted scoped access.
- `admin`: Company-wide administrative operations, user lifecycle, exception reviews.
- `manager`: Team lead access, reviewing direct/team reports, approving team attendance requests.
- `employee`: Standard access to own profile, personal attendance, posts, and certificates.
- `intern`: Standard access with lifecycle expiry and designated mentor/manager visibility.

## Permission Codes

| Permission Code | Description | Authorized Roles |
| :--- | :--- | :--- |
| `directory.people.view` | View employee directory listing | All authenticated |
| `directory.people.manage` | Create, edit, suspend, or exit employees | `admin`, `super_admin` |
| `attendance.self.check_in` | Record personal attendance check-in | All active accounts |
| `attendance.self.check_out` | Record personal attendance check-out | All active accounts |
| `attendance.team.review` | Review team attendance and exceptions | `manager`, `admin`, `super_admin` |
| `attendance.admin.correct` | Perform administrative corrections | `admin`, `super_admin` |
| `posts.self.create` | Create daily work post | All active accounts |
| `posts.team.view` | View team feed and permitted posts | Permitted team members / leads |
| `certificates.issue` | Issue certificates and generate PDFs | `admin`, `super_admin` |
| `certificates.verify` | Public verification of certificate tokens | Anonymous (Public) |
| `core.settings.manage` | Modify workspace configuration | `super_admin` |
