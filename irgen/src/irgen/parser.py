import logging
import re
from typing import Any

import jpype
import polars as pl

from irgen.attribute import (
    get_access_value,
    get_modified_write_value,
    get_read_action_value,
)


def parse_dataframe(df: pl.DataFrame) -> pl.DataFrame:
    parsed_df = (
        df.with_columns(
            header_reg=pl.first("REG").over("ADDR"),
            start_addr_str=pl.first("ADDR").over("ADDR"),
            stride=(
                pl.col("WIDTH")
                .filter(pl.col("FIELD").is_not_null() & (pl.col("FIELD") != ""))
                .sum()
                .over("ADDR")
                // 8  # 1Byte = 8bits
            ),
        )
        .with_columns(
            is_expandable=pl.col("header_reg").str.contains(r"\{n\}"),
            base_reg_name=pl.coalesce(
                pl.col("header_reg").str.extract(r"(.*?)\{n\}"), pl.lit("")
            ),
            n_start=pl.col("header_reg")
            .str.extract(r"n\s*=\s*(\d+)", 1)
            .cast(pl.Int64),
            n_end=pl.col("header_reg").str.extract(r"~\s*(\d+)", 1).cast(pl.Int64),
            start_addr_int=pl.col("start_addr_str")
            .str.extract(r"0x([0-9a-fA-F]+)")
            .str.to_integer(base=16, strict=True),
        )
        .with_columns(
            n_series=pl.when(
                pl.col("is_expandable")
                & pl.col("n_start").is_not_null()
                & pl.col("n_end").is_not_null()
            )
            .then(pl.int_ranges(pl.col("n_start"), pl.col("n_end") + 1))
            .otherwise(pl.lit(None))
        )
        .explode("n_series")
        .filter(
            (pl.col("is_expandable") & pl.col("n_series").is_not_null())
            | (
                ~pl.col("is_expandable")
                & pl.col("FIELD").is_not_null()
                & (pl.col("FIELD") != "")
            )
        )
        .with_columns(
            ADDR=pl.when(pl.col("is_expandable"))
            .then(
                (
                    pl.col("start_addr_int") + pl.col("n_series") * pl.col("stride")
                ).map_elements(lambda x: f"0x{x:X}", return_dtype=pl.String)
            )
            .otherwise(pl.col("ADDR")),
            REG=pl.when(pl.col("is_expandable"))
            .then(pl.col("base_reg_name") + "_" + pl.col("n_series").cast(pl.String))
            .otherwise(pl.col("REG")),
        )
    )

    parsed_df = parsed_df.select(
        "ADDR",
        "REG",
        "FIELD",
        "BIT",
        "WIDTH",
        "ATTRIBUTE",
        "DEFAULT",
        "DESCRIPTION",
        "stride",
    )

    return parsed_df


def process_vendor_sheet(df: pl.DataFrame, object_factory: Any) -> Any:
    """Process the Sheet<vendor> to create an IP-XACT Component object"""
    try:
        component = object_factory.createComponentType()
        component.setVendor(str(df["VENDOR"][0]))
        component.setLibrary(str(df["LIBRARY"][0]))
        component.setName(str(df["NAME"][0]))
        component.setVersion(str(df["VERSION"][0]))

        return component
    except (pl.exceptions.PolarsError, ValueError, KeyError) as e:
        logging.error(f"Failed to process the Sheet<vendor>: {e}")
        return None
    except Exception as e:
        logging.error(
            f"An unexpected error occurred while processing the Sheet<vendor>: {e}"
        )
        return None


def process_address_map_sheet(
    df: pl.DataFrame, object_factory: Any, ipxact_version: str
) -> list[Any]:
    """Process the Sheet<address_map> to create a list of IP-XACT AddressBlock objects."""

    if not jpype.isJVMStarted():
        raise

    BigInteger = jpype.JClass("java.math.BigInteger")
    address_blocks = []
    for row in df.iter_rows(named=True):
        try:
            if ipxact_version == "1685-2009":
                base_address = object_factory.createBaseAddress()
            else:
                base_address = object_factory.createUnsignedLongintExpression()
            base_address.setValue(str(row["OFFSET"]))
            if ipxact_version == "1685-2009":
                block_range = object_factory.createBankedBlockTypeRange()
            else:
                block_range = object_factory.createUnsignedPositiveLongintExpression()
            block_range.setValue(str(row["RANGE"]))
            if ipxact_version == "1685-2022":
                width = object_factory.createUnsignedPositiveIntExpression()
                width.setValue("32")
            elif ipxact_version == "1685-2014":
                width = object_factory.createUnsignedIntExpression()
                width.setValue("32")
            else:
                width = object_factory.createBankedBlockTypeWidth()
                width.setValue(BigInteger.valueOf(32))
            address_block = object_factory.createAddressBlockType()
            address_block.setName(str(row["BLOCK"]))
            address_block.setBaseAddress(base_address)
            address_block.setRange(block_range)
            address_block.setWidth(width)
            address_blocks.append(address_block)
        except KeyError as e:
            logging.error(
                f"Missing expected column in address_map sheet: {e}. Skipping row: {row}"
            )
    return address_blocks


def process_register_sheet(
    df: pl.DataFrame, object_factory: Any, ipxact_version: str
) -> list[Any]:
    """Process a single register block sheet into a list of Register objects."""

    if not jpype.isJVMStarted():
        raise

    match ipxact_version:
        case "1685-2009":
            AccessType = jpype.JClass("org.ieee.ipxact.v2009.AccessType")
        case "1685-2014":
            AccessType = jpype.JClass("org.ieee.ipxact.v2014.AccessType")
            ModifiedWriteValueType = jpype.JClass(
                "org.ieee.ipxact.v2014.ModifiedWriteValueType"
            )
            ReadActionType = jpype.JClass("org.ieee.ipxact.v2014.ReadActionType")
        case "1685-2022":
            AccessType = jpype.JClass("org.ieee.ipxact.v2022.AccessType")
            ModifiedWriteValueType = jpype.JClass(
                "org.ieee.ipxact.v2022.ModifiedWriteValueType"
            )
            ReadActionType = jpype.JClass("org.ieee.ipxact.v2022.ReadActionType")
        case _:
            raise ValueError(f"Unsupported IP-XACT version: {ipxact_version}")

    BigInteger = jpype.JClass("java.math.BigInteger")

    try:
        # Pre-process the dataframe
        filled_df = df.select(pl.all().forward_fill())
        logging.debug(f"filled_df is {filled_df}")
        parsed_df = parse_dataframe(filled_df)
        logging.debug(f"parsed_df is {parsed_df}")
    except pl.exceptions.PolarsError as e:
        logging.error(f"Polars error during pre-processing of a register sheet: {e}")
        return []

    registers = []
    # Group by register to process all its fields together
    for reg_name, group in parsed_df.group_by("REG", maintain_order=True):
        if not reg_name:
            logging.warning("Skipping rows with no register name.")
            continue

        fields: list[Any] = []
        first_row = group.row(0, named=True)

        total_field_reset = 0

        for field_row in group.iter_rows(named=True):
            try:
                bit_match = re.findall(r"\[(?:\d+:)?(\d+)]", str(field_row["BIT"]))
                if not bit_match:
                    raise ValueError(
                        f"Could not parse bit offset from '{field_row['BIT']}"
                    )

                if re.match(r"^(rsvd|reserved)\d*$", str(field_row["FIELD"])):
                    continue

                field = object_factory.createFieldType()
                if ipxact_version != "1685-2009":
                    bit_offset = object_factory.createUnsignedIntExpression()
                    bit_offset.setValue(str(bit_match[0]))
                if ipxact_version != "1685-2009":
                    bit_width = object_factory.createUnsignedPositiveIntExpression()
                    bit_width.setValue(str(field_row["WIDTH"]))
                else:
                    bit_width = object_factory.createFieldTypeBitWidth()
                    bit_width.setValue(BigInteger.valueOf(int(field_row["WIDTH"])))
                field.setName(str(field_row["FIELD"]))
                if ipxact_version != "1685-2009":
                    field.setBitOffset(bit_offset)
                else:
                    field.setBitOffset(BigInteger.valueOf(int(bit_match[0])))
                field.setBitWidth(bit_width)
                if ipxact_version == "1685-2022":
                    access_policies = (
                        object_factory.createFieldTypeFieldAccessPolicies()
                    )
                    access_policy = object_factory.createFieldTypeFieldAccessPoliciesFieldAccessPolicy()
                    access_policy_list = access_policies.getFieldAccessPolicy()
                if (
                    access_value := get_access_value(str(field_row["ATTRIBUTE"]))
                ) is not None:
                    if ipxact_version == "1685-2022":
                        access_policy.setAccess(AccessType.fromValue(access_value))
                    else:
                        field.setAccess(AccessType.fromValue(access_value))
                if (
                    modified_write_value := get_modified_write_value(
                        str(field_row["ATTRIBUTE"])
                    )
                ) is not None:
                    if ipxact_version == "1685-2022":
                        modified_write = object_factory.createModifiedWriteValue()
                    elif ipxact_version == "1685-2014":
                        modified_write = (
                            object_factory.createFieldTypeModifiedWriteValue()
                        )
                    if ipxact_version != "1685-2009":
                        modified_write.setValue(
                            ModifiedWriteValueType.fromValue(modified_write_value)
                        )
                    if ipxact_version == "1685-2022":
                        access_policy.setModifiedWriteValue(modified_write)
                    elif ipxact_version == "1685-2014":
                        field.setModifiedWriteValue(modified_write)
                    else:
                        field.setModifiedWriteValue(modified_write_value)
                if (
                    read_action_value := get_read_action_value(
                        str(field_row["ATTRIBUTE"])
                    )
                ) is not None:
                    if ipxact_version == "1685-2022":
                        read_action = object_factory.createReadAction()
                    elif ipxact_version == "1685-2014":
                        read_action = object_factory.createFieldTypeReadAction()
                    if ipxact_version != "1685-2009":
                        read_action.setValue(
                            ReadActionType.fromValue(read_action_value)
                        )
                    if ipxact_version == "1685-2022":
                        access_policy.setReadAction(read_action)
                    elif ipxact_version == "1685-2014":
                        field.setReadAction(read_action)
                    else:
                        field.setReadAction(read_action_value)
                if ipxact_version == "1685-2022":
                    access_policy_list.add(access_policy)
                    field.setFieldAccessPolicies(access_policies)
                if ipxact_version != "1685-2009":
                    resets = object_factory.createFieldTypeResets()
                    reset = object_factory.createReset()
                    reset_value = object_factory.createUnsignedBitVectorExpression()
                    reset_value.setValue(str(field_row["DEFAULT"]))
                    reset.setValue(reset_value)
                    reset_list = resets.getReset()
                    reset_list.add(reset)
                    field.setResets(resets)
                fields.append(field)

                total_field_reset += int(field_row["DEFAULT"], 16) << int(bit_match[0])
            except (KeyError, ValueError, TypeError) as e:
                logging.error(
                    f"Skipping invalid field '{field_row.get('FIELD', 'N/A')}' in register '{reg_name[0]}': {e}"
                )

        if fields:
            register = object_factory.createRegisterFileRegister()
            if ipxact_version != "1685-2009":
                address_offset = object_factory.createUnsignedLongintExpression()
                address_offset.setValue(str(first_row["ADDR"]))
                register_size = object_factory.createUnsignedPositiveIntExpression()
                register_size.setValue(
                    str(int(first_row["stride"]) * 8)
                )  # stride: Byte
            else:
                register_size = object_factory.createRegisterFileRegisterSize()
                register_size.setValue(BigInteger.valueOf(int(first_row["stride"]) * 8))
            register.setName(str(reg_name[0]))
            if ipxact_version != "1685-2009":
                register.setAddressOffset(address_offset)
            else:
                register.setAddressOffset(str(first_row["ADDR"]))
            register.setSize(register_size)
            if ipxact_version == "1685-2009":
                reg_reset = object_factory.createRegisterFileRegisterReset()
                reg_reset_value = object_factory.createRegisterFileRegisterResetValue()
                reg_reset_value.setValue(hex(total_field_reset))
                reg_reset.setValue(reg_reset_value)
                register.setReset(reg_reset)
            field_list = register.getField()
            for field in fields:
                field_list.add(field)
            registers.append(register)
    return registers
