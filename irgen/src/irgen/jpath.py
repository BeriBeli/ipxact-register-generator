import sys
import logging
from pathlib import Path

import jpype

from irgen.config import SCHEMA_JAR


def is_bundled() -> bool:
    return hasattr(sys, "_MEIPASS")


def get_class_path() -> list[str]:
    if is_bundled():
        meipass: str = getattr(sys, "_MEIPASS")
        schema_jar_path = Path(meipass) / "jar" / SCHEMA_JAR
        dependency_path = Path(meipass) / "jar" / "dependency"
    else:
        schema_jar_path = (
            Path(__file__).parent.parent.parent.parent
            / "schema"
            / "target"
            / SCHEMA_JAR
        ).resolve()
        dependency_path = (
            Path(__file__).parent.parent.parent.parent
            / "schema"
            / "target"
            / "dependency"
        ).resolve()

    logging.debug(f"jar_path: {schema_jar_path}")
    logging.debug(f"dependency_path: {dependency_path}")

    if not schema_jar_path.exists():
        raise FileNotFoundError(f"Could not find {schema_jar_path}")
    if not dependency_path.exists():
        raise FileNotFoundError(f"Could not find {dependency_path}")
    try:
        dependency_jars = dependency_path.glob("*.jar")
    except Exception as e:
        raise FileNotFoundError(f"Could not find dependency JARs: {e}")

    class_path = [str(schema_jar_path)] + [str(jar) for jar in dependency_jars]

    return class_path


def get_jvm_path() -> str:
    library = {
        "win32": "bin/server/jvm.dll",
        "darwin": "lib/server/libjvm.dylib",
    }.get(sys.platform, "lib/server/libjvm.so")

    if is_bundled():
        meipass: str = getattr(sys, "_MEIPASS")
        if (Path(meipass) / "jre").exists():
            return str(Path(meipass) / "jre" / library)
        else:
            logging.warning("No custom JVM path provided, using default JVM path.")

    return jpype.getDefaultJVMPath()
