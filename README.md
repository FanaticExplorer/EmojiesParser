# EmojisParser

EmojisParser is a Python-based tool designed to download emojis and stickers from almost any public server.

## Features

- **Emoji Parsing**: Extract emojis from multiple directories and organize them efficiently.
- **Sticker Parsing**: Handle stickers alongside emojis.
- **JSON Response Handling**: Parse and process JSON response files for metadata.
- **Output Management**: Organize parsed assets into structured output directories.


## Usage

1. Clone the repository to your local machine:

   ```bash
   git clone https://github.com/FanaticExplorer/EmojiesParser.git
   cd EmojiesParser
   ```

2. Install the dependencies:

   Using pip:
   ```bash
   pip install .
   ```
   
   Or using `uv`:
   ```bash
   uv sync
   ```

3. Run the script:

   ```bash
   python main.py
   ```
   
   Or using `uv`:
   ```bash
   uv run main.py
   ```

## Directory Structure

The project follows this structure:

```
EmojiesParser/
├── main.py
├── ...
├── output/
│   ├── archlinux/
│   │   ├── response.json
│   │   ├── emojis/
│   │   └── stickers/
│   └── ...
```

## Contributing

Contributions are welcome! If you have ideas for new features or improvements, feel free to open an issue or submit a pull request.

## License

This project is licensed under the MIT License. See the LICENSE file for details.
