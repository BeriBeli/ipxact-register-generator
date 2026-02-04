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


def _parse_n_series(header: str) -> list[int] | None:
    if not header or "{n}" not in header:
        return None

    match = re.search(r"n\s*=\s*range\(([^)]*)\)", header)
    if match:
        args = [arg.strip() for arg in match.group(1).split(",") if arg.strip()]
        try:
            if len(args) == 1:
                start, end, step = 0, int(args[0]), 1
            elif len(args) == 2:
                start, end, step = int(args[0]), int(args[1]), 1
            elif len(args) == 3:
                start, end, step = int(args[0]), int(args[1]), int(args[2])
            else:
                return None
        except ValueError:
            return None
        if step == 0:
            return None
        return list(range(start, end, step))

    match = re.search(r"n\s*=\s*(\d+)\s*~\s*(\d+)", header)
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        return list(range(start, end + 1))

    match = re.search(r"n\s*=\s*(\d+)", header)
    if match:
        end = int(match.group(1))
        return list(range(0, end))

    return None


def _parse_default_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    try:
        return int(text, 0)
    except ValueError:
        return None


def _parse_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return text


def _parse_bit_range(value: Any) -> tuple[int, int] | None:
    text = _parse_text(value)
    if not text:
        return None
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    text = text.replace(" ", "")
    if ":" in text:
        hi_text, lo_text = text.split(":", 1)
    else:
        hi_text, lo_text = text, text
    try:
        hi = int(hi_text)
        lo = int(lo_text)
    except ValueError:
        return None
    return (hi, lo)


def _parse_bit_high(value: Any) -> int | None:
    bit_range = _parse_bit_range(value)
    if bit_range is None:
        return None
    return bit_range[0]


def _parse_bit_low(value: Any) -> int | None:
    bit_range = _parse_bit_range(value)
    if bit_range is None:
        return None
    return bit_range[1]


def _set_description(obj: Any, value: Any) -> None:
    text = _parse_text(value)
    if not text:
        return
    setter = getattr(obj, "setDescription", None)
    if setter is None:
        return
    try:
        setter(text)
    except Exception as e:
        logging.warning(f"Failed to set description: {e}")


def _validate_columns(df: pl.DataFrame, required: set[str], sheet_name: str) -> bool:
    missing = required - set(df.columns)
    if missing:
        missing_list = ", ".join(sorted(missing))
        logging.error(f"Missing required columns in sheet '{sheet_name}': {missing_list}")
        return False
    return True


def parse_dataframe(df: pl.DataFrame) -> pl.DataFrame:
    stride_override_expr = (
        pl.col("STRIDE").cast(pl.Int64, strict=False)
        if "STRIDE" in df.columns
        else pl.lit(None, dtype=pl.Int64)
    )
    reg_size_bits_expr = (
        pl.col("REG_SIZE").cast(pl.Int64, strict=False)
        if "REG_SIZE" in df.columns
        else pl.lit(None, dtype=pl.Int64)
    )

    base_stride_expr = pl.when(pl.col("stride_bits").is_not_null()).then(
        (pl.col("stride_bits") + 1 + 7) // 8
    ).otherwise((pl.col("width_sum_bits") + 7) // 8)

    stride_expr = pl.when(stride_override_expr.is_not_null()).then(
        stride_override_expr
    ).otherwise(
        pl.when(reg_size_bits_expr.is_not_null())
        .then((reg_size_bits_expr + 7) // 8)
        .otherwise(base_stride_expr)
    )

    parsed_df = (
        df.with_columns(
            header_reg=pl.first("REG").over("ADDR"),
            start_addr_str=pl.first("ADDR").over("ADDR"),
            bit_hi=pl.col("BIT").map_elements(
                _parse_bit_high, return_dtype=pl.Int64
            ),
            width_sum_bits=(
                pl.col("WIDTH")
                .filter(pl.col("FIELD").is_not_null() & (pl.col("FIELD") != ""))
                .sum()
                .over("ADDR")
            ),
        )
        .with_columns(
            is_expandable=pl.col("header_reg").str.contains(r"\{n\}"),
            base_reg_name=pl.coalesce(
                pl.col("header_reg").str.extract(r"(.*?)\{n\}"), pl.lit("")
            ),
            start_addr_int=pl.col("start_addr_str")
            .str.extract(r"0x([0-9a-fA-F]+)")
            .str.to_integer(base=16, strict=True),
            n_series=pl.col("header_reg").map_elements(
                _parse_n_series, return_dtype=pl.List(pl.Int64)
            ),
            stride_bits=(
                pl.col("bit_hi")
                .filter(
                    pl.col("FIELD").is_not_null()
                    & (pl.col("FIELD") != "")
                    & pl.col("bit_hi").is_not_null()
                )
                .max()
                .over("ADDR")
            ),
        )
        .with_columns(stride=stride_expr)
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
    if not _validate_columns(df, {"VENDOR", "LIBRARY", "NAME", "VERSION"}, "vendor"):
        return None
    try:
        component = object_factory.createComponentType()
        component.setVendor(str(df["VENDOR"][0]))
        component.setLibrary(str(df["LIBRARY"][0]))
        component.setName(str(df["NAME"][0]))
        component.setVersion(str(df["VERSION"][0]))
        _set_description(component, df["DESCRIPTION"][0] if "DESCRIPTION" in df.columns else None)

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
    if not _validate_columns(df, {"BLOCK", "OFFSET", "RANGE"}, "address_map"):
        return []

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
            _set_description(address_block, row.get("DESCRIPTION"))
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
    if not _validate_columns(df, {"ADDR", "REG", "FIELD", "BIT", "WIDTH"}, "register"):
        return []

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
        fill_cols = [pl.col("ADDR").forward_fill(), pl.col("REG").forward_fill()]
        if "STRIDE" in df.columns:
            fill_cols.append(pl.col("STRIDE").forward_fill())
        if "REG_SIZE" in df.columns:
            fill_cols.append(pl.col("REG_SIZE").forward_fill())
        filled_df = df.with_columns(*fill_cols)
        logging.debug(f"filled_df is {filled_df}")
        parsed_df = parse_dataframe(filled_df)
        logging.debug(f"parsed_df is {parsed_df}")
    except pl.exceptions.PolarsError as e:
        logging.error(f"Polars error during pre-processing of a register sheet: {e}")
        return []

    registers = []
    # Group by register to process all its fields together
    for reg_name, group in parsed_df.group_by("REG", maintain_order=True):
        reg_key = reg_name[0] if isinstance(reg_name, tuple) else reg_name
        if not reg_key:
            logging.warning("Skipping rows with no register name.")
            continue

        fields: list[Any] = []
        first_row = group.row(0, named=True)
        _set_description_from_row = first_row.get("DESCRIPTION")

        total_field_reset = 0

        for field_row in group.iter_rows(named=True):
            try:
                bit_range = _parse_bit_range(field_row["BIT"])
                if not bit_range:
                    raise ValueError(
                        f"Could not parse bit offset from '{field_row['BIT']}"
                    )

                if re.match(r"^(rsvd|reserved)\d*$", str(field_row["FIELD"])):
                    continue

                field = object_factory.createFieldType()
                bit_low = bit_range[1]
                if ipxact_version != "1685-2009":
                    bit_offset = object_factory.createUnsignedIntExpression()
                    bit_offset.setValue(str(bit_low))
                if ipxact_version != "1685-2009":
                    bit_width = object_factory.createUnsignedPositiveIntExpression()
                    bit_width.setValue(str(field_row["WIDTH"]))
                else:
                    bit_width = object_factory.createFieldTypeBitWidth()
                    bit_width.setValue(BigInteger.valueOf(int(field_row["WIDTH"])))
                field.setName(str(field_row["FIELD"]))
                _set_description(field, field_row.get("DESCRIPTION"))
                if ipxact_version != "1685-2009":
                    field.setBitOffset(bit_offset)
                else:
                    field.setBitOffset(BigInteger.valueOf(int(bit_low)))
                field.setBitWidth(bit_width)

                attribute = str(field_row.get("ATTRIBUTE", "")).strip()
                access_policy_used = False
                if ipxact_version == "1685-2022":
                    access_policies = (
                        object_factory.createFieldTypeFieldAccessPolicies()
                    )
                    access_policy = object_factory.createFieldTypeFieldAccessPoliciesFieldAccessPolicy()

                try:
                    access_value = get_access_value(attribute)
                except KeyError:
                    access_value = None
                    if attribute:
                        logging.warning(
                            f"Unknown access attribute '{attribute}' in register '{reg_key}'."
                        )
                if access_value is not None:
                    if ipxact_version == "1685-2022":
                        access_policy.setAccess(AccessType.fromValue(access_value))
                        access_policy_used = True
                    else:
                        field.setAccess(AccessType.fromValue(access_value))

                try:
                    modified_write_value = get_modified_write_value(attribute)
                except KeyError:
                    modified_write_value = None
                    if attribute:
                        logging.warning(
                            f"Unknown modified write attribute '{attribute}' in register '{reg_key}'."
                        )
                if modified_write_value is not None:
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
                        access_policy_used = True
                    elif ipxact_version == "1685-2014":
                        field.setModifiedWriteValue(modified_write)
                    else:
                        field.setModifiedWriteValue(modified_write_value)

                try:
                    read_action_value = get_read_action_value(attribute)
                except KeyError:
                    read_action_value = None
                    if attribute:
                        logging.warning(
                            f"Unknown read action attribute '{attribute}' in register '{reg_key}'."
                        )
                if read_action_value is not None:
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
                        access_policy_used = True
                    elif ipxact_version == "1685-2014":
                        field.setReadAction(read_action)
                    else:
                        field.setReadAction(read_action_value)

                if ipxact_version == "1685-2022" and access_policy_used:
                    access_policy_list = access_policies.getFieldAccessPolicy()
                    access_policy_list.add(access_policy)
                    field.setFieldAccessPolicies(access_policies)

                default_raw = field_row.get("DEFAULT")
                default_int = _parse_default_int(default_raw)
                if ipxact_version != "1685-2009" and default_int is not None:
                    try:
                        resets = object_factory.createFieldTypeResets()
                        reset = object_factory.createReset()
                        reset_value = object_factory.createUnsignedBitVectorExpression()
                        reset_value.setValue(str(default_raw))
                        reset.setValue(reset_value)
                        reset_list = resets.getReset()
                        reset_list.add(reset)
                        field.setResets(resets)
                    except Exception as e:
                        logging.warning(
                            f"Failed to set reset value '{default_raw}' in register '{reg_key}': {e}"
                        )

                fields.append(field)

                if default_int is not None:
                    width_int = _parse_int(field_row.get("WIDTH"))
                    if width_int is not None and width_int > 0:
                        mask = (1 << width_int) - 1
                        total_field_reset += (default_int & mask) << int(bit_low)
            except (KeyError, ValueError, TypeError) as e:
                logging.error(
                    f"Skipping invalid field '{field_row.get('FIELD', 'N/A')}' in register '{reg_key}': {e}"
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
            register.setName(str(reg_key))
            _set_description(register, _set_description_from_row)
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
