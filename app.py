from flask import Flask, request, jsonify
import gitlab
import os
from urllib.parse import urlparse, unquote

app = Flask(__name__)

# IMPORTANT: Set your GitLab API base URL.
# If you are using gitlab.com, it's 'https://gitlab.com/api/v4'
# If you are using a self-hosted instance, change this to your instance's API URL, e.g., 'https://your-company-gitlab.com/api/v4'
GITLAB_API_BASE_URL = os.environ.get('GITLAB_API_BASE_URL', 'https://gitlab.com/api/v4')

# Helper function to get project ID or full path from a URL
def get_project_path_from_url(repo_url):
    """
    Extracts the 'namespace/project_path' from a GitLab repository URL.
    Handles common URL formats.
    """
    try:
        parsed_url = urlparse(repo_url)
        path_parts = parsed_url.path.strip('/').split('/')

        # Heuristic for common GitLab URL structures:
        # e.g., https://gitlab.com/namespace/project
        # e.g., https://gitlab.com/namespace/subgroup/project
        # Also handles .git suffix

        if len(path_parts) >= 2:
            # Join all parts after the domain to form the full path
            full_path = '/'.join(path_parts)
            # Remove '.git' suffix if present
            if full_path.endswith('.git'):
                full_path = full_path[:-4]
            return unquote(full_path) # URL-decode any parts
        else:
            raise ValueError("Invalid GitLab repository URL format.")
    except Exception as e:
        print(f"Error parsing URL {repo_url}: {e}")
        return None

# Existing GitLab client initialization
def get_gitlab_client(gitlab_pat, repo_url):
    # This ensures the client is initialized with the correct base URL
    # derived from the input repo_url's domain, if it's different from default.
    parsed_repo_url = urlparse(repo_url)
    gitlab_host = f"{parsed_repo_url.scheme}://{parsed_repo_url.netloc}"
    
    # Use the extracted host for the GitLab client, then append /api/v4
    # This makes it flexible for self-hosted instances.
    client_url = f"{gitlab_host}/api/v4"

    try:
        gl = gitlab.Gitlab(client_url, private_token=gitlab_pat)
        gl.auth() # Test authentication
        return gl
    except gitlab.exceptions.GitlabError as e:
        raise ValueError(f"GitLab API Error: {e}")
    except Exception as e:
        raise ValueError(f"Failed to connect to GitLab: {e}")


# New API endpoint for fetching repository details
@app.route('/api/repo_details', methods=['POST'])
def get_repo_details():
    data = request.get_json()
    repo_url = data.get('repoUrl')
    gitlab_pat = data.get('gitlabPat')

    if not repo_url or not gitlab_pat:
        return jsonify({"error": "Repository URL and GitLab PAT are required."}), 400

    try:
        # Get the GitLab client
        gl = get_gitlab_client(gitlab_pat, repo_url)
        
        # Extract project path (e.g., 'group/subgroup/project-name')
        project_path_with_namespace = get_project_path_from_url(repo_url)
        if not project_path_with_namespace:
            return jsonify({"error": "Could not parse repository URL. Please ensure it's a valid GitLab project URL."}), 400

        # Use the Projects API to get project details by its path with namespace
        # Note: 'id' parameter can accept both integer ID or URL-encoded path
        project = gl.projects.get(project_path_with_namespace, statistics=True, license=True)

        # Extract desired details from the project object
        details = {
            "id": project.id,
            "name": project.name,
            "path_with_namespace": project.path_with_namespace,
            "description": project.description,
            "web_url": project.web_url,
            "default_branch": project.default_branch,
            "visibility": project.visibility,
            "created_at": project.created_at,
            "last_activity_at": project.last_activity_at,
            "forks_count": project.forks_count,
            "star_count": project.star_count,
            "open_issues_count": project.open_issues_count,
            "archived": project.archived,
            "empty_repo": project.empty_repo,
            "avatar_url": project.avatar_url,
            "statistics": {
                "commit_count": project.statistics.get('commit_count'),
                "storage_size": project.statistics.get('storage_size'), # in bytes
                "repository_size": project.statistics.get('repository_size'),
                "wiki_size": project.statistics.get('wiki_size'),
                "lfs_objects_size": project.statistics.get('lfs_objects_size'),
                "build_artifacts_size": project.statistics.get('build_artifacts_size')
            },
            "license": project.license.get('name') if project.license else None
        }

        return jsonify(details), 200

    except gitlab.exceptions.GitlabError as e:
        status_code = e.response_code
        error_message = e.error_message if hasattr(e, 'error_message') and e.error_message else str(e)
        if status_code == 404:
            return jsonify({"error": f"Project not found or access denied for URL: {repo_url}. Details: {error_message}"}), 404
        elif status_code == 401:
            return jsonify({"error": f"Authentication failed for GitLab API. Please check your Personal Access Token (PAT) and ensure it has 'read_repository' or 'api' scope. Details: {error_message}"}), 401
        else:
            return jsonify({"error": f"GitLab API Error ({status_code}): {error_message}"}), status_code
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return jsonify({"error": f"An unexpected server error occurred: {str(e)}"}), 500

# ... (rest of your existing app.py code for /api/branches, /api/diff, /api/file_content)

if __name__ == '__main__':
    # You might want to remove debug=True in production
    app.run(debug=True, port=5000)