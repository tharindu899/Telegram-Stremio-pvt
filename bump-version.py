import re
import sys
from pathlib import Path

def bump_version(version: str, part: str) -> str:
    major, minor, patch = map(int, version.split("."))
    if part == "patch":
        patch += 1
    elif part == "minor":
        minor += 1
        patch = 0
    elif part == "major":
        major += 1
        minor = patch = 0
    else:
        raise ValueError(f"Unknown part to bump: {part}")
    return f"{major}.{minor}.{patch}"

def update_pyproject(path: Path, new_version: str):
    content = path.read_text()
    updated_content = re.sub(
        r'version\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+"',
        f'version = "{new_version}"',
        content
    )
    path.write_text(updated_content)
    print(f"Updated pyproject.toml to version {new_version}")

def update_init(path: Path, new_version: str):
    content = path.read_text()
    updated_content = re.sub(
        r'__version__\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+"',
        f'__version__ = "{new_version}"',
        content
    )
    path.write_text(updated_content)
    print(f"Updated Backend/__init__.py to version {new_version}")

def main(part: str = "patch"):
    pyproject_path = Path("pyproject.toml")
    init_path = Path("Backend/__init__.py")

    if not pyproject_path.exists() or not init_path.exists():
        print("Error: pyproject.toml or Backend/__init__.py not found.")
        sys.exit(1)

    # Read current version from pyproject.toml
    content = pyproject_path.read_text()
    match = re.search(r'version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', content)
    if not match:
        print("Error: version not found in pyproject.toml")
        sys.exit(1)

    current_version = match.group(1)
    new_version = bump_version(current_version, part)

    update_pyproject(pyproject_path, new_version)
    update_init(init_path, new_version)

if __name__ == "__main__":
    part = sys.argv[1] if len(sys.argv) > 1 else "patch"  # default bump is patch
    main(part)
