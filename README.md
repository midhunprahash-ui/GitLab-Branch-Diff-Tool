# GitLab Branch Compare Tool

A web-based tool to compare branches in a GitLab repository, providing clear insights into commit differences and file changes within specified date ranges.

---

## Features

**Repository and Authentication**
- Input GitLab repository URL.
- Optionally provide a Personal Access Token (PAT) for private repositories.
- Fetch and populate available branches from the specified repository.

**Branch Selection**
- Select a **Source Branch** and a **Destination Branch** for comparison.

**Date Range Filtering**
- Specify **From Date** and **To Date** to filter commits and file differences.
- Only changes within this date range are displayed.

**Commit Comparison**
- Side-by-side list of commits for both branches.
- Highlights commits unique to each branch within the date range.
- Displays commit hash, message, author, and date.

**File Differences**
- Lists files **Added**, **Modified**, or **Deleted** between the branches within the date range.
- Clearly labels files with their change type.

**Side-by-Side File Content Diff**
- Click on a file in the **File Differences** list to open a modal.
- View a side-by-side comparison of file content with highlighted additions, deletions, and unchanged lines.

---

## Tech Stack

### Frontend
- **HTML5**: Page structure.
- **Tailwind CSS**: Utility-first CSS for a clean, responsive UI.
- **JavaScript (Vanilla JS)**: Handles client-side logic and dynamic UI.

### Backend
- **Python 3**: Core server-side language.
- **Flask**: Lightweight framework to build RESTful API endpoints.
- **Requests**: Makes HTTP requests to the GitLab API.
- **Flask-CORS**: Enables Cross-Origin Resource Sharing for frontend-backend communication.

### API
- **GitLab API**: Interacts with repositories, fetches branches, retrieves commit history, and compares states.

---

## How to Run (Local Development)

### Prerequisites
- Python 3.x installed.
- `pip` (Python package installer).

### Clone the Repository

```bash
# Example:
# git clone <repository-url>
# cd <repository-name>
```
### Install dependencies

```
pip instal requirements.txt
```
### Run the Backend Server

```
python app.py -> MAC
py app.py     -> Windows
```
                                                     ```Developed with 🧠 by Midhun.```
