# Keep the tests away from the user's real preferences and tool library.
import os
import tempfile

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="alphalink-test-config-")
os.environ.pop("ALPHALINK_LANG", None)
