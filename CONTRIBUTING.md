# Contributing to SEOCHO

Thank you for your interest in contributing to **SEOCHO**! We welcome contributions from the community to help make this the best scalable GraphRAG framework.

## 🤝 How to Contribute

### 1. Reporting Issues
* **Bugs**: Please use the [GitHub Issues](https://github.com/tteon/seocho/issues) tracker. Describe the bug in detail, including steps to reproduce and your environment (OS, Docker version).
* **Feature Requests**: Open an issue with the label `enhancement`. Explain the use case and how it fits into the SEOCHO architecture.

### 2. Pull Request (PR) Process
1. **Fork** the repository to your own GitHub account.
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/<your-username>/seocho.git
   ```
3. **Create a Branch** for your feature or fix:
   ```bash
   git checkout -b feature/my-new-feature
   # or
   git checkout -b fix/bug-description
   ```
4. **Make Changes**. Keep them focused and atomic.
5. **Test Your Changes**.
   * Run the test suite:
     ```bash
     docker-compose exec extraction-service pytest tests/
     ```
   * Verify the UI works at `http://localhost:8501`.
6. **Commit** with meaningful messages:
   * Use [Conventional Commits](https://www.conventionalcommits.org/) (e.g., `feat: add new vector tool`, `fix: resolve cycle in router`).
7. **Push** to your fork.
8. **Open a Pull Request** against the `main` (or `graphrag-dev`) branch of the original repository.

---

## 💻 Coding Standards

### Python (Backend)
* **Style**: We follow [PEP 8](https://peps.python.org/pep-0008/).
* **Type Hinting**: Please use Python type hints for all function signatures.
  ```python
  def my_function(param: str) -> bool: ...
  ```
* **Async**: The Agent Server uses `asyncio`. Ensure new IO-bound tools are async-compatible where possible (or wrapped correctly).

### Streamlit (Frontend)
* Keep the UI logic inside `evaluation/app.py` clean.
* Use `st.session_state` for state management.
* Follow the split-screen pattern (Chat Left, Graph Right).

---

## 🧪 Testing Requirements
* **New Features**: Must include at least one unit test or integration test in `extraction/tests/`.
* **Bug Fixes**: Should include a regression test ensuring the bug doesn't return.

## 📜 License
By contributing, you agree that your contributions will be licensed under the MIT License defined in this repository.
