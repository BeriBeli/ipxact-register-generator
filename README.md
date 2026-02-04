# IP-XACT Register Generator

A Python utility to automatically convert register description spreadsheets into IP-XACT XML files.

This script leverages the Eclipse Implementation of [JAXB](https://eclipse-ee4j.github.io/jaxb-ri) to generate the necessary IP-XACT schema bindings. See `example.xlsx` for a sample input file.

> _Additional information for `example.xlsx`_
> 1. **Hexadecimal** uses "**0x**" as a **prefix**, **decimal** does **not have a prefix**, and the use of **'h** and **'d** to denote numbers in Verilog/SystemVerilog is **prohibited**
> 2. Multiple register groups are denoted as "**rega{n}, n=0~3**" (inclusive) or "**rega{n}, n=range(0,4)**" (Python `range` semantics). "`n=3`" is treated as `range(3)`.
> 3. Default registers are **32 bits**
> 4. Optional columns `REG_SIZE` (bits) or `STRIDE` (bytes) can override auto-sized register width/stride.

## 🛠️ Getting Started

Follow these instructions to get the project up and running on your local machine.

### Prerequisites

Make sure you have the following software installed:

- **Python**: `3.10` or newer
- **Java**: Version `21` (*Tested*)
- **Maven**: [Apache Maven](https://maven.apache.org/)
- **uv**: A fast Python package installer from [Astral](https://docs.astral.sh/uv/)

### Installation & Build

You can build the project using the provided scripts or by following the manual steps.

> [!WARNING!] Ensure that the $JAVA_HOME variable is set in your environment.

#### Method 1: Using Build Scripts

The easiest way to get started is to use the platform-specific build script.

- **Linux / macOS**:

  ```shell
  source build.sh
  ```

- **Windows**:

  ```powerShell
  .\build.ps1

#### Method 2: Manual Installation

If you prefer, follow these steps to build and install the components manually.

1. **Build the Java Components**:

   ```shell
   # Navigate to the java directory
   cd schema
   # Clean, package, and copy dependencies
   mvn clean package
   mvn dependency:copy-dependencies
   # Return to the root directory
   cd ..
   ```

3. **Set up the Python Environment**:

   ```shell
   # Navigate to the python directory
   cd irgen
   
   # Create and activate a virtual environment (using uv is recommended)
   # For uv:
   uv venv
   source .venv/bin/activate  # On Linux/macOS
   # .venv\Scripts\activate   # On Windows
   uv sync
   
   # For standard venv:
   # python3 -m venv .venv
   # source .venv/bin/activate  # On Linux/macOS
   # .venv\Scripts\activate   # On Windows
   # pip install -e .
   
   # Return to the root directory
   cd ..
   ```

## 💻 Usage

Run the generator from the command line using the `irgen` command. Provide the path to your Excel file and specify an output path for the XML.

**Example:**

```shell
# source irgen/.venv/bin/irgen or .\irgen\.venv\Scripts\irgen
irgen --excel example.xlsx -o example.xml
```

### Command-Line Options

| Option                   | Alias | Description                                                  | default      |
| ------------------------ | ----- | ------------------------------------------------------------ | ------------ |
| `--help`                 | `-h`  | Show the help message and exit.                              |              |
| `--version`              | `-v`  | Version.                                                     |              |
| `--debug`                | `-d`  | Enable debug logging for detailed output.                    |              |
| `--template`             | `-t`  | Generate a template excel for an example.                    |              |
| `--excel <path>`         | `-e`  | Path to the input Excel file.                                |              |
| `--output <path>`        | `-o`  | Path for the output XML file.                                |              |
| `--vendor-sheet <name>`  |       | Name of the sheet containing vendor extensions.              | version      |
| `--address-sheet <name>` |       | Name of the sheet containing the address map.                | address_map  |
| `--ipxact-version <ver>` |       | IP-XACT version (e.g., `1685-2009`, `1685-2014`, `1685-2022`). | 1685-2014    |

## Formatting and Validation

After generating the XML file, you can use `xmllint`(provided by libxml2, Generally pre-installed on Linux and macOS) to format and validate it against the official Accellera schema.

1. **Format the XML File**:

```shell
xmllint --format <your_output.xml> -o <formatted_output.xml>
```

2. **Validate the XML File**: 

   You can validate against the online schema or a local copy.

    - **Validate using Online Schema**:

      validate output.xml against the 1685-2014 schema for an example.

      ```shell
      xmllint --noout --schema "http://www.accellera.org/XMLSchema/IPXACT/1685-2014/index.xsd" <your_output.xml>
      ```

    - **Validate using Local Schema**: 

      First, download the schema files from [Accellera](http://www.accellera.org/XMLSchema/)

      ```shell
      xmllint --noout --schema /path/to/your/local/schema/index.xsd <your_output.xml>
      ```

## 📜 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for more details.
