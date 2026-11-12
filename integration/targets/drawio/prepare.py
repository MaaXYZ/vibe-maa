"""Repository convenience wrapper around the installed target provisioner."""

from vibe_maa.targets.drawio.prepare import main, manifest, provision

__all__ = ["manifest", "provision"]


if __name__ == "__main__":
    main()
