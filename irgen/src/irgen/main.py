import sys
import logging
import argparse
from pathlib import Path
from typing import Any

import polars as pl
import fastexcel
import jpype

from irgen.__version__ import __version__
from irgen.jpath import get_class_path, get_jvm_path
from irgen.parser import (
    process_vendor_sheet,
    process_address_map_sheet,
    process_register_sheet,
)
from irgen.template import generate_template
from irgen.config import (
    DEBUG,
    DEFAULT_ADDRESS_SHEET,
    DEFAULT_IPXACT_VERSION,
    DEFAULT_VENDOR_SHEET,
    LOG_FILE,
)


def setup_logger_level(debug: bool):
    """Configures logging based on the debug flag."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(levelname)s] %(message)s",
        filename=LOG_FILE if debug else None,
        filemode="w",
    )


def setup_arg_parser() -> argparse.ArgumentParser:
    """Set up and return the argument parser."""
    parser = argparse.ArgumentParser(
        description="Convert spreadsheets register maps to IP-XACT XML files."
    )
    parser.add_argument(
        "-v",
        "--version",
        action="store_true",
        help="Version",
    )
    parser.add_argument(
        "-d",
        "--debug",
        default=DEBUG,
        action="store_true",
        help="Enable debug logging.",
    )
    parser.add_argument(
        "-t",
        "--template",
        action="store_true",
        help="Generate a template excel for an example.",
    )
    parser.add_argument(
        "-e",
        "--excel",
        help="Path to the input excel file.",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Path for the output XML file.",
    )
    parser.add_argument(
        "--vendor-sheet",
        default=DEFAULT_VENDOR_SHEET,
        help="Name of the vendor sheet.",
    )
    parser.add_argument(
        "--address-sheet",
        default=DEFAULT_ADDRESS_SHEET,
        help="Name of the address map sheet.",
    )
    parser.add_argument(
        "--ipxact-version",
        default=DEFAULT_IPXACT_VERSION,
        help="IP-XACT version to use (e.g., 1685-2009, 1685-2014, 1685-2022)",
    )
    return parser


def main():
    parser = setup_arg_parser()
    args = parser.parse_args()
    setup_logger_level(args.debug)

    if args.version:
        print(__version__)
        sys.exit(0)

    if args.template:
        generate_template()
        sys.exit(0)

    if not args.excel:
        parser.error(
            "the --excel argument is REQUIRED in this context.\n"
            "Hint: Use -t or --template to generate an example Excel file."
        )

    excel_name = str(args.excel)

    if args.output:
        xml_path = str(args.output)
    else:
        xml_path = str(f"{Path(args.excel).stem}.xml")

    vendor_sheet = str(args.vendor_sheet)
    address_sheet = str(args.address_sheet)
    ipxact_version = str(args.ipxact_version)

    if ipxact_version not in ["1685-2009", "1685-2014", "1685-2022"]:
        logging.critical(f"Unsupported IP-XACT version: {ipxact_version}!")
        sys.exit(1)

    try:
        sheet_names = fastexcel.read_excel(excel_name).sheet_names
    except (fastexcel.FastExcelError, FileNotFoundError) as e:
        logging.critical(f"Could not read Excel file '{excel_name}': {e}")
        sys.exit(1)

    try:
        logging.debug("Starting JVM...")
        jpype.startJVM(jvmpath=get_jvm_path(), classpath=get_class_path())
        logging.debug("JVM Started.")

        # import Java classes
        match ipxact_version:
            case "1685-2009":
                ObjectFactory = jpype.JClass("org.ieee.ipxact.v2009.ObjectFactory")
            case "1685-2014":
                ObjectFactory = jpype.JClass("org.ieee.ipxact.v2014.ObjectFactory")
            case "1685-2022":
                ObjectFactory = jpype.JClass("org.ieee.ipxact.v2022.ObjectFactory")
            case _:
                logging.critical(f"Unsupported IP-XACT version: {ipxact_version}")
                sys.exit(1)

        XmlGenerator = jpype.JClass("org.example.XmlGenerator")
        IpXactVersion = jpype.JClass("org.example.IpXactVersion")

        logging.debug("Java classes imported successfully.")

        object_factory = ObjectFactory()
        component = None
        address_blocks: list[Any] = []
        all_registers: dict[str, list[Any]] = {}

        logging.info(f"Processing sheets: {sheet_names}")
        for sheet_name in sheet_names:
            logging.info(f"--- Reading sheet: {sheet_name} ---")
            try:
                df = pl.read_excel(excel_name, sheet_name=sheet_name)
            except Exception as e:
                logging.error(f"Could not read sheet '{sheet_name}' with Polars: {e}")
                continue

            if sheet_name == vendor_sheet:
                component = process_vendor_sheet(df, object_factory)
            elif sheet_name == address_sheet:
                address_blocks = process_address_map_sheet(
                    df, object_factory, ipxact_version
                )
            else:
                all_registers[sheet_name] = process_register_sheet(
                    df, object_factory, ipxact_version
                )

        if not component:
            logging.critical("Failed to parse vendor information. Aborting.")
            sys.exit(1)
        if not address_blocks:
            logging.critical("Failed to parse address blocks. Aborting.")
            sys.exit(1)

        # Assemble the final component data structure
        logging.info("Assembling final component structure...")
        address_block_names = {block.getName() for block in address_blocks}
        extra_register_sheets = [
            name for name in all_registers.keys() if name not in address_block_names
        ]
        for sheet_name in extra_register_sheets:
            logging.warning(
                f"Register sheet '{sheet_name}' has no matching address block in '{address_sheet}'."
            )
        for block in address_blocks:
            if block.getName() in all_registers:
                if ipxact_version != "1685-2009":
                    register_list = block.getRegisterData()
                else:
                    register_list = block.getRegister()
                for reg in all_registers[block.getName()]:
                    register_list.add(reg)
                logging.info(
                    f"Mapped {len(register_list)} registers to address block '{block.getName()}'."
                )
            else:
                logging.warning(
                    f"No register block sheet found for address block '{block.getName()}'."
                )

        memory_map = object_factory.createMemoryMapType()
        memory_map.setName(component.getName())
        address_block_list = memory_map.getMemoryMap()
        for block in address_blocks:
            address_block_list.add(block)
        memory_maps = object_factory.createMemoryMaps()
        memory_map_list = memory_maps.getMemoryMap()
        memory_map_list.add(memory_map)
        component.setMemoryMaps(memory_maps)

        logging.info(f"XML file will be generated at: {xml_path}")
        XmlGenerator.generateXml(
            component, IpXactVersion.fromValue(ipxact_version), xml_path
        )

    except Exception as e:
        logging.critical(f"An error occurred during processing: {e}")
        sys.exit(1)
    finally:
        if jpype.isJVMStarted():
            logging.debug("Shutting down JVM...")
            jpype.shutdownJVM()
            logging.debug("JVM shut down.")


if __name__ == "__main__":
    main()
