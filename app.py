from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import gitlab
import os

# Initialize Flask app, specifying the templates folder
app = Flask(__name__)
CORS(app) # Enable CORS for all origins

def get_gitlab_client(gitlab_url, personal_access_token):
    """Initializes and returns a GitLab client."""
    if not personal_access_token:
        app.logger.error("GitLab Personal Access Token is missing.")
        return None
    try:
        gl = gitlab.Gitlab(gitlab_url, private_token=personal_access_token)
        gl.auth() # Test authentication
        app.logger.info(f"Successfully authenticated with GitLab at {gitlab_url}")
        return gl
    except gitlab.exceptions.GitlabError as e:
        app.logger.error(f"GitLab authentication error for URL {gitlab_url}: {e}")
        return None

@app.route('/')
def index():
    # Use render_template to serve index.html from the templates folder
    return render_template('index.html')

@app.route('/api/branches', methods=['POST'])
def get_branches():
    data = request.json
    repo_url = data.get('repoUrl')
    personal_access_token = data.get('personalAccessToken')

    if not repo_url:
        return jsonify({"error": "Repository URL is required."}), 400
    if not personal_access_token:
        return jsonify({"error": "Personal Access Token is required for authentication."}), 401

    try:
        # Normalize the URL for GitLab API
        if repo_url.startswith('git@'):
            repo_url = repo_url.replace('git@', 'https://').replace(':', '/').replace('.git', '')

        # Ensure the URL is clean (remove trailing slash if present)
        if repo_url.endswith('/'):
            repo_url = repo_url[:-1]

        parts = repo_url.split('/')

        # Handle cases like https://gitlab.com/project or https://gitlab.com/group/project
        # We need at least 3 parts for the base URL (protocol, empty, domain)
        # And then the project path starts from the 4th part.
        if len(parts) < 4: # e.g., https://gitlab.com/project (4 parts: https:, , gitlab.com, project)
            return jsonify({"error": "Invalid GitLab repository URL format. Expected at least 'https://domain.com/project'."}), 400

        gitlab_url_base = '/'.join(parts[:3])
        project_path_with_namespace = '/'.join(parts[3:])

        # Remove .git suffix from project_path_with_namespace if present
        if project_path_with_namespace.endswith('.git'):
            project_path_with_namespace = project_path_with_namespace[:-4] # Remove last 4 characters (.git)

        app.logger.info(f"Parsed GitLab URL Base: {gitlab_url_base}")
        app.logger.info(f"Parsed Project Path with Namespace (after .git removal): {project_path_with_namespace}")

        gl = get_gitlab_client(gitlab_url_base, personal_access_token)
        if not gl:
            return jsonify({"error": "Failed to authenticate with GitLab. Check your PAT."}), 401

        project = gl.projects.get(project_path_with_namespace)
        branches = project.branches.list(all=True)
        branch_names = [branch.name for branch in branches]
        return jsonify({"branches": branch_names})
    except gitlab.exceptions.GitlabError as e:
        app.logger.error(f"GitLab API error fetching branches for {repo_url}: {e}")
        if "404 Project Not Found" in str(e):
            return jsonify({"error": f"Repository not found or access denied for URL: {repo_url}. Ensure the URL is correct and the PAT has sufficient permissions."}), 404
        return jsonify({"error": f"Failed to fetch branches: {e}"}), 500
    except Exception as e:
        app.logger.error(f"An unexpected error occurred in get_branches: {e}")
        return jsonify({"error": f"An unexpected error occurred: {e}"}), 500

@app.route('/api/diff', methods=['POST'])
def get_diff():
    data = request.json
    repo_url = data.get('repoUrl')
    source_branch = data.get('sourceBranch')
    target_branch = data.get('targetBranch')
    personal_access_token = data.get('personalAccessToken')

    if not repo_url or not source_branch or not target_branch:
        return jsonify({"error": "Repository URL, source branch, and target branch are required."}), 400
    if not personal_access_token:
        return jsonify({"error": "Personal Access Token is required for authentication."}), 401

    try:
        if repo_url.startswith('git@'):
            repo_url = repo_url.replace('git@', 'https://').replace(':', '/').replace('.git', '')
        if repo_url.endswith('/'):
            repo_url = repo_url[:-1]

        parts = repo_url.split('/')
        if len(parts) < 4:
            return jsonify({"error": "Invalid GitLab repository URL format. Expected at least 'https://domain.com/project'."}), 400

        gitlab_url_base = '/'.join(parts[:3])
        project_path_with_namespace = '/'.join(parts[3:])

        # Remove .git suffix from project_path_with_namespace if present
        if project_path_with_namespace.endswith('.git'):
            project_path_with_namespace = project_path_with_namespace[:-4]

        app.logger.info(f"Diff - Parsed GitLab URL Base: {gitlab_url_base}")
        app.logger.info(f"Diff - Parsed Project Path with Namespace (after .git removal): {project_path_with_namespace}")


        gl = get_gitlab_client(gitlab_url_base, personal_access_token)
        if not gl:
            return jsonify({"error": "Failed to authenticate with GitLab. Check your PAT."}), 401

        project = gl.projects.get(project_path_with_namespace)

        diff_result = project.repository_compare(source_branch, target_branch)

        full_diff_string = ""
        if diff_result and 'diffs' in diff_result:
            for d in diff_result['diffs']:
                # Add a and b paths, index, and modes for proper diff formatting
                old_path = d.get('old_path', 'N/A')
                new_path = d.get('new_path', 'N/A')
                full_diff_string += f"diff --git a/{old_path} b/{new_path}\n"

                # Add index line if available (GitLab API often provides a_mode/b_mode)
                if 'a_mode' in d and 'b_mode' in d:
                    full_diff_string += f"index {d['a_mode']}..{d['b_mode']}"
                    if 'new_file' in d and d['new_file']:
                        full_diff_string += " 100644" # Standard mode for new files if not explicitly given
                    elif 'renamed_file' in d and d['renamed_file']:
                        full_diff_string += f" {d.get('new_file_mode', '100644')}" # Use new_file_mode for renamed
                    full_diff_string += "\n"

                # Add original diff content
                full_diff_string += d.get('diff', '') + "\n"

        return jsonify({"diff": full_diff_string.strip()})
    except gitlab.exceptions.GitlabError as e:
        app.logger.error(f"GitLab API error fetching diff for {repo_url}: {e}")
        if "404 Project Not Found" in str(e):
            return jsonify({"error": f"Repository not found or access denied for URL: {repo_url}."}), 404
        return jsonify({"error": f"Failed to fetch diff: {e}"}), 500
    except Exception as e:
        app.logger.error(f"An unexpected error occurred in get_diff: {e}")
        return jsonify({"error": f"An unexpected error occurred: {e}"}), 500


@app.route('/api/file_content', methods=['POST'])
def get_file_content():
    data = request.json
    repo_url = data.get('repoUrl')
    branch_name = data.get('branchName')
    file_path = data.get('filePath')
    personal_access_token = data.get('personalAccessToken')

    if not repo_url or not branch_name or not file_path:
        return jsonify({"error": "Repository URL, branch name, and file path are required."}), 400
    if not personal_access_token:
        return jsonify({"error": "Personal Access Token is required for authentication."}), 401

    try:
        if repo_url.startswith('git@'):
            repo_url = repo_url.replace('git@', 'https://').replace(':', '/').replace('.git', '')
        if repo_url.endswith('/'):
            repo_url = repo_url[:-1]

        parts = repo_url.split('/')
        if len(parts) < 4:
            return jsonify({"error": "Invalid GitLab repository URL format. Expected at least 'https://domain.com/project'."}), 400

        gitlab_url_base = '/'.join(parts[:3])
        project_path_with_namespace = '/'.join(parts[3:])

        # Remove .git suffix from project_path_with_namespace if present
        if project_path_with_namespace.endswith('.git'):
            project_path_with_namespace = project_path_with_namespace[:-4]

        app.logger.info(f"File Content - Parsed GitLab URL Base: {gitlab_url_base}")
        app.logger.info(f"File Content - Parsed Project Path with Namespace (after .git removal): {project_path_with_namespace}")


        gl = get_gitlab_client(gitlab_url_base, personal_access_token)
        if not gl:
            return jsonify({"error": "Failed to authenticate with GitLab. Check your PAT."}), 401

        project = gl.projects.get(project_path_with_namespace)
        file = project.files.get(file_path=file_path, ref=branch_name)
        content = file.decode() # content is base64 encoded by default
        return jsonify({"content": content})
    except gitlab.exceptions.GitlabError as e:
        app.logger.error(f"GitLab API error fetching file content for {repo_url}: {e}")
        if "404 Not Found" in str(e) or "400 Bad Request" in str(e): # Common for file not found
            return jsonify({"error": f"File '{file_path}' not found on branch '{branch_name}' or access denied."}), 404
        return jsonify({"error": f"Failed to fetch file content: {e}"}), 500
    except Exception as e:
        app.logger.error(f"An unexpected error occurred in get_file_content: {e}")
        return jsonify({"error": f"An unexpected error occurred: {e}"}), 500

# Removed the /api/repo_details endpoint as requested.

if __name__ == '__main__':
    app.run(debug=True, port=5000)
