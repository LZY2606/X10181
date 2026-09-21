import os
import tempfile

os.environ["GRSB_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
