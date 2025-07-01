from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import os
import requests
from urllib.parse import urlparse, quote 
import re

app = Flask(__name__)
CORS(app)


GITLAB_API_BASE_URL = 'https://gitlab.com/api/v4'



def get_project_id(repo_url, gitlab_pat):
    """
    Parses a GitLab repository URL and resolves its project ID using the provided PAT.
    It uses the hardcoded GITLAB_API_BASE_URL.
    """
    parsed_url = urlparse(repo_url)
    path_parts = [part for part in parsed_url.path.split('/') if part]

    if not path_parts:
        raise ValueError("Invalid GitLab repository URL: No path found.")

    project_path_with_namespace = '/'.join(path_parts).replace('.git', '')

    headers = {'Private-Token': gitlab_pat}

    encoded_path = quote(project_path_with_namespace, safe='') # Use quote for URL encoding
    project_api_url = f'{GITLAB_API_BASE_URL}/projects/{encoded_path}'

    print(f"Resolving project ID for path: {project_path_with_namespace} using {project_api_url}")
    try:
        response = requests.get(project_api_url, headers=headers)
        response.raise_for_status()
        project_data = response.json()
        return project_data['id']
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            raise ValueError(f"GitLab project not found for URL: {repo_url}. "
                             "Please check the URL and ensure the PAT has sufficient access.")
        elif e.response.status_code == 401:
            raise ValueError("Authentication failed for GitLab API. Please check your Personal Access Token (PAT).")
        elif e.response.status_code == 403:
            raise ValueError("GitLab API rate limit or access forbidden. Please wait or check PAT permissions.")
        else:
            raise Exception(f"GitLab API error resolving project ID: {e.response.status_code} - {e.response.text}")
    except Exception as e:
        raise Exception(f"An unexpected error occurred while resolving GitLab project ID: {str(e)}")


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/branches', methods=['POST'])
def get_branches():
    data = request.get_json()
    repo_url = data.get('repoUrl')
    gitlab_pat = data.get('gitlabPat')

    if not all([repo_url, gitlab_pat]):
        return jsonify({"error": "Repository URL and GitLab Personal Access Token are required."}), 400

    try:
        project_id = get_project_id(repo_url, gitlab_pat)
        headers = {'Private-Token': gitlab_pat}

        branches_api_url = f'{GITLAB_API_BASE_URL}/projects/{project_id}/repository/branches'

        print(f"Fetching branches from: {branches_api_url}")
        response = requests.get(branches_api_url, headers=headers)
        response.raise_for_status()

        branches_data = response.json()
        branches = [branch['name'] for branch in branches_data]

        if 'main' in branches:
            branches.insert(0, branches.pop(branches.index('main')))
        elif 'master' in branches:
            branches.insert(0, branches.pop(branches.index('master')))

        return jsonify({"branches": branches})

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        error_message = f"GitLab API error: {status_code} - {e.response.text}"
        if status_code == 404:
            error_message = "Branches not found for the project. Please check the URL and project ID."
        elif status_code == 401:
            error_message = "Authentication failed. Please check your Personal Access Token (PAT)."
        elif status_code == 403:
            error_message = "API rate limit exceeded or access forbidden. Please wait or check PAT permissions."
        print(f"Error in get_branches: {error_message}")
        return jsonify({"error": error_message}), status_code
    except Exception as e:
        print(f"Error in get_branches: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/diff', methods=['POST'])
def get_diff():
    data = request.get_json()
    repo_url = data.get('repoUrl')
    branch1 = data.get('branch1')
    branch2 = data.get('branch2')
    gitlab_pat = data.get('gitlabPat')

    if not all([repo_url, branch1, branch2, gitlab_pat]):
        return jsonify({"error": "Repository URL, branch1, branch2, and GitLab Personal Access Token are required."}), 400

    try:
        project_id = get_project_id(repo_url, gitlab_pat)
        headers = {'Private-Token': gitlab_pat}

        compare_api_url = f'{GITLAB_API_BASE_URL}/projects/{project_id}/repository/compare'
        params = {
            'from': branch1,
            'to': branch2
        }

        print(f"Comparing branches: {branch1} and {branch2} in project {project_id} using {compare_api_url}")
        response = requests.get(compare_api_url, headers=headers, params=params)
        response.raise_for_status()

        diff_data = response.json()
        raw_diff_content = diff_data.get('diff', '')

        if not raw_diff_content and 'diffs' in diff_data and diff_data['diffs']:
            print("Warning: 'diff' field not found directly, concatenating from 'diffs' array.")
            concatenated_diff = []
            for file_diff in diff_data['diffs']:
                concatenated_diff.append(f"--- a/{file_diff['old_path']}")
                concatenated_diff.append(f"+++ b/{file_diff['new_path']}")
                concatenated_diff.append(f"@@ -{file_diff['old_pos']} +{file_diff['new_pos']} @@")
                concatenated_diff.append(file_diff.get('diff', ''))
            raw_diff_content = "\n".join(concatenated_diff)
        elif not raw_diff_content:
            raw_diff_content = ""


        return jsonify({"diff": raw_diff_content})

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        error_message = f"GitLab API error: {status_code} - {e.response.text}"
        if status_code == 404:
            error_message = "One or both branches do not exist or the project is not found. Please check branch names, URL, and ensure PAT has access."
        elif status_code == 401:
            error_message = "Authentication failed. Please check your Personal Access Token (PAT)."
        elif status_code == 403:
            error_message = "API rate limit exceeded or access forbidden. Please wait or check PAT permissions."
        print(f"Error in get_diff: {error_message}")
        return jsonify({"error": error_message}), status_code
    except Exception as e:
        print(f"Error in get_diff: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/file_content', methods=['POST'])
def get_file_content():
    data = request.get_json()
    repo_url = data.get('repoUrl')
    file_path = data.get('filePath')
    ref = data.get('ref') # ref is the branch name or commit SHA
    gitlab_pat = data.get('gitlabPat')

    if not all([repo_url, file_path, ref, gitlab_pat]):
        return jsonify({"error": "Repository URL, File Path, Branch/Ref, and GitLab Personal Access Token are required."}), 400

    try:
        project_id = get_project_id(repo_url, gitlab_pat)
        headers = {'Private-Token': gitlab_pat}

        # URL-encode the file_path and ref as they can contain slashes or special characters
        encoded_file_path = quote(file_path, safe='')
        encoded_ref = quote(ref, safe='')

        file_content_api_url = f'{GITLAB_API_BASE_URL}/projects/{project_id}/repository/files/{encoded_file_path}/raw?ref={encoded_ref}'

        print(f"Fetching file content for '{file_path}' from branch/ref '{ref}' in project {project_id}")
        response = requests.get(file_content_api_url, headers=headers)
        response.raise_for_status()

        # GitLab API for raw file content directly returns the text/binary content
        file_content = response.text

        return jsonify({"content": file_content})

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        error_message = f"GitLab API error fetching file: {status_code} - {e.response.text}"
        if status_code == 404:
            error_message = "File not found at the specified path/branch, or project not found. Please check file path, branch, URL, and ensure PAT has access."
        elif status_code == 401:
            error_message = "Authentication failed. Please check your Personal Access Token (PAT)."
        elif status_code == 403:
            error_message = "API rate limit exceeded or access forbidden. Please wait or check PAT permissions."
        print(f"Error in get_file_content: {error_message}")
        return jsonify({"error": error_message}), status_code
    except Exception as e:
        print(f"Error in get_file_content: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')