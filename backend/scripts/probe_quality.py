"""Small real Docker-only harness probe. No credentials or production database rows."""

from app.config import settings
from app.db import new_id
from app.execution import DockerSandbox

dev = DockerSandbox(new_id())
result, snapshot = dev.run(
    "run_command",
    {
        "command": "git init -b main && git config user.name Probe && git config user.email probe@localhost && printf '<div id=app>42</div>' > index.html && printf 'dist\\n' > .gitignore && git add index.html .gitignore && git commit -m probe && mkdir dist && cp index.html dist/index.html && git rev-parse HEAD"
    },
    None,
)
assert result["ok"], result
sha = result["stdout"].splitlines()[-1]
tests = [
    {
        "criterion_id": "AC-001",
        "name": "Real browser probe",
        "script": "assert.equal(await page.locator('#app').textContent(),'42');",
    }
]
result, _ = DockerSandbox(new_id(), image_name=settings().quality_image).run(
    "run_tests", {"expected_sha": sha, "tests": tests}, snapshot
)
print(result)
