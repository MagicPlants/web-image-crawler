# Contributing to Web Image Crawler & Archive Manager

First off, thank you for considering contributing to the Web Image Crawler & Archive Manager by Joe Montgomery ([Magic Plants](https://magicplants.net))! It's people like you that make open source such a great community.

---

## 🧭 Code of Conduct

We are committed to providing a friendly, safe, and welcoming environment for all. Please be respectful, constructive, and collaborative in all discussions and pull requests.

---

## 🛠️ How Can I Contribute?

### 1. Reporting Bugs
- Ensure the bug was not already reported by searching existing [GitHub Issues](https://github.com).
- If you're unable to find an open issue addressing the problem, open a new one.
- Include a **clear title and description**, steps to reproduce, the URL being scraped (if public), and the error traceback or log output.

### 2. Suggesting Enhancements
- Open a GitHub Issue with the `enhancement` label.
- Clearly describe the feature, why it would be useful, and how you imagine it working.

### 3. Submitting Pull Requests

1. **Fork the repository** on GitHub.
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/MagicPlants/web-image-crawler.git
   cd web-image-crawler
   ```
3. **Create a topic branch** from `main`:
   ```bash
   git checkout -b feature/my-cool-feature
   ```
4. **Make your changes** and test thoroughly:
   - Ensure the GUI launches without errors (`python gui.py`).
   - Test CLI options (`python crawler.py --help`).
5. **Commit your changes** with clear, descriptive commit messages:
   ```bash
   git commit -m "Add support for custom user-agent strings in GUI"
   ```
6. **Push to your fork**:
   ```bash
   git push origin feature/my-cool-feature
   ```
7. **Open a Pull Request** against the `main` branch of the upstream repository.

---

## 🎨 Coding Standards

- **PEP 8**: Follow standard Python conventions.
- **Type Annotations**: Use type hints for function arguments and return types wherever appropriate.
- **Cross-Platform Compatibility**: Use `os.path` / `pathlib` and ensure file operations work on Windows, macOS, and Linux.
- **Non-blocking UI**: Any long-running network or crawling operations in `gui.py` must run in background threads to keep the UI smooth and responsive.

---

Thank you for your contributions!
