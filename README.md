# Verovo website

Live: https://roy-tong.github.io/Verovo/
Current public release: v0.20.0.

Static HTML, CSS and JavaScript. Verovo is presented inside the unified OVO website as independent economic intelligence for AI agents: Agent Spend Healthcheck, Independent Bill Audit and Agent Economics Optimization. This directory retains buyer guides, examples, services, claims and the Verovo engineering blog; the independent visual homepage has been replaced by a redirect to `https://roy-tong.github.io/ovo/en.html`. Buyer services and fee mechanisms are under validation. Local developer tools and the existing free measurement runs retain their separate scope. Standards, SDK, claims and research remain supporting resources.

## Pages

- `index.html`: redirect to the unified OVO homepage; deep product guides and examples remain available.
- `first-look.html`: three commissioning tasks — period charges and delivery, control effectiveness, private procurement comparison.
- `examples/buyer-review.html`: generated synthetic output of the actual `review --sample` command.
- `tools.html`: cataloged domestic/overseas tool bill coverage, buyer vs seller audit, local CDP collection and research cases. Cataloged is not live-validated.
- `services.html`: canonical service terms, input requirements, application links and refunds.
- `faq.html`: Chinese FAQ (GEO for domestic AI search): real query words answered with repository-sourced numbers, FAQPage JSON-LD, honest boundaries (0 live-validated, no recovery promise).
- `claims.html`: dated technical evidence; no paid-adoption claims.
- `releases.html`: CLI and SDK release information; package versions are independent.
- `first-look.html`: buyer task-fit discussion, evidence requirements and exploratory fee mechanisms; no standard package or recovery promise.
- `recount.html`, `rules/`, `blog/`, `badges.html`: existing tools and research; preserve their evidence boundaries.
- `llms.txt`, `sitemap.xml`, `robots.txt`: public discovery. Repository documents in llms.txt use absolute GitHub URLs.

## Preview and publish

```bash
cd website
python3 -m http.server 4173
```

Edit on `main`. Run AGENTS.md checks and inspect local links before pushing. `.github/workflows/conformance.yml` verifies the repository, updates `status.json`, then mirrors `website/` to the root of `gh-pages`. GitHub Pages publishes that branch. Do not manually overwrite the deployment branch. Verify the conformance run, Pages deployment and live content after pushing.

## Keep current

Use the service page as the public source for pricing and delivery conditions. Do not replace it with an older workspace copy. Date audit statistics and link evidence; do not use engineering counts as proof of customer value. CLI and SDK versions must be checked independently. Preserve historical release notes as historical, rather than rewriting past releases.
