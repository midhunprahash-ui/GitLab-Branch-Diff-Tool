from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
from urllib.parse import quote, urlparse
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

GITLAB_API_BASE_URL = "https://gitlab.com/api/v4"
LOG_MAX_COMMITS = 10000 

def get_project_id_from_url(repo_url):
    """
    Extracts the URL-encoded project path (which acts as project ID) from a GitLab repository URL.
    e.g., 'https://gitlab.com/group/subgroup/project.git' -> 'group%2Fsubgroup%2Fproject'
    """
    parsed_url = urlparse(repo_url)
    path_parts = [part for part in parsed_url.path.split('/') if part]

    if not path_parts:
        raise ValueError("Invalid GitLab repository URL format. Could not extract project path.")


    project_path = '/'.join(path_parts)
    if project_path.endswith('.git'):
        project_path = project_path[:-4]

    return quote(project_path, safe='')


def make_gitlab_api_request(endpoint, pat=None, params=None):
    """
    Makes a GET request to the GitLab API.
    Handles authentication with PAT and basic error checking.
    """
    headers = {}
    if pat:
        headers['Private-Token'] = pat

    url = f"{GITLAB_API_BASE_URL}{endpoint}"
    print(f"Making GitLab API request to: {url} with params: {params} (PAT provided: {bool(pat)})")

    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status() 
        return response.json()
    except requests.exceptions.Timeout:
        raise Exception(f"GitLab API request timed out for endpoint: {endpoint}")
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        error_detail = e.response.text
        if status_code == 401:
            raise Exception(f"Authentication failed (401 Unauthorized). Please check your PAT.")
        elif status_code == 403:
            raise Exception(f"Access forbidden (403 Forbidden). Insufficient permissions or repository is private.")
        elif status_code == 404:
            raise Exception(f"Resource not found (404 Not Found). Check repository URL or branch names. Detail: {error_detail}")
        else:
            raise Exception(f"GitLab API error {status_code}: {error_detail}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Network error or invalid GitLab API URL: {e}")
    except Exception as e:
        raise Exception(f"An unexpected error occurred during API request: {e}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/branches', methods=['POST'])
def get_branches():
    """Fetches and returns a list of branches from a GitLab repository using GitLab API."""
    data = request.get_json()
    repo_url = data.get('repoUrl')
    pat = data.get('pat')

    print(f"Received request for branches: repoUrl={repo_url}, PAT provided={bool(pat)}")

    if not repo_url:
        return jsonify({"error": "Repository URL is required."}), 400

    try:
        project_id = get_project_id_from_url(repo_url)
        branches_data = make_gitlab_api_request(
            f'/projects/{project_id}/repository/branches',
            pat=pat,
            params={'per_page': 100} 
        )

        branches = [branch['name'] for branch in branches_data]
        branches.sort() 

    
        if 'main' in branches:
            branches.insert(0, branches.pop(branches.index('main')))
        elif 'master' in branches:
            branches.insert(0, branches.pop(branches.index('master')))

        print(f"Successfully fetched {len(branches)} branches.")
        return jsonify({"branches": branches})

    except Exception as e:
        print(f"Error in get_branches: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/compare_commits', methods=['POST'])
def compare_commits():
    """Fetches and returns commit logs for source and destination branches using GitLab API."""
    data = request.get_json()
    repo_url = data.get('repoUrl')
    pat = data.get('pat')
    source_branch = data.get('sourceBranch')
    destination_branch = data.get('destinationBranch')

    print(f"Received request for comparing commits: repoUrl={repo_url}, sourceBranch={source_branch}, destinationBranch={destination_branch}, PAT provided={bool(pat)}")

    if not all([repo_url, source_branch, destination_branch]):
        return jsonify({"error": "Repository URL, sourceBranch, and destinationBranch are required."}), 400

    try:
        project_id = get_project_id_from_url(repo_url)

   
        source_commits_data = make_gitlab_api_request(
            f'/projects/{project_id}/repository/commits',
            pat=pat,
            params={'ref_name': source_branch, 'per_page': LOG_MAX_COMMITS}
        )
        source_commits = [{
            "hash": commit['id'],
            "message": commit['title'], 
            "author": commit['author_name'],
            "date": commit['authored_date'] 
        } for commit in source_commits_data]
        print(f"Found {len(source_commits)} commits in source branch.")

        
        destination_commits_data = make_gitlab_api_request(
            f'/projects/{project_id}/repository/commits',
            pat=pat,
            params={'ref_name': destination_branch, 'per_page': LOG_MAX_COMMITS}
        )
        destination_commits = [{
            "hash": commit['id'],
            "message": commit['title'],
            "author": commit['author_name'],
            "date": commit['authored_date']
        } for commit in destination_commits_data]
        print(f"Found {len(destination_commits)} commits in destination branch.")

        return jsonify({
            "source_commits": source_commits,
            "destination_commits": destination_commits
        })

    except Exception as e:
        print(f"Error in compare_commits: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/compare_files', methods=['POST'])
def compare_files():
    """
    Compares files between two branches using GitLab API's compare endpoint,
    and lists all files in each branch by traversing their trees.
    """
    data = request.json
    repo_url = data.get('repoUrl')
    pat = data.get('pat')
    source_branch = data.get('sourceBranch')
    destination_branch = data.get('destinationBranch')

    print(f"Received request for file comparison: repoUrl={repo_url}, sourceBranch={source_branch}, destinationBranch={destination_branch}, PAT provided={bool(pat)}")

    if not all([repo_url, source_branch, destination_branch]):
        return jsonify({"error": "Missing repository URL, source branch, or destination branch"}), 400

    try:
        project_id = get_project_id_from_url(repo_url)

        
        source_branch_info = make_gitlab_api_request(
            f'/projects/{project_id}/repository/branches/{quote(source_branch, safe="")}',
            pat=pat
        )
        source_commit_id = source_branch_info['commit']['id']

       
        source_tree_data = make_gitlab_api_request(
            f'/projects/{project_id}/repository/tree',
            pat=pat,
            params={'ref': source_commit_id, 'recursive': True, 'per_page': 1000} 
        )
        source_files = sorted([item['path'] for item in source_tree_data if item['type'] == 'blob'])
        print(f"Found {len(source_files)} files in source branch.")


        
        destination_branch_info = make_gitlab_api_request(
            f'/projects/{project_id}/repository/branches/{quote(destination_branch, safe="")}',
            pat=pat
        )
        destination_commit_id = destination_branch_info['commit']['id']

        destination_tree_data = make_gitlab_api_request(
            f'/projects/{project_id}/repository/tree',
            pat=pat,
            params={'ref': destination_commit_id, 'recursive': True, 'per_page': 1000}
        )
        destination_files = sorted([item['path'] for item in destination_tree_data if item['type'] == 'blob'])
        print(f"Found {len(destination_files)} files in destination branch.")


      
        compare_data = make_gitlab_api_request(
            f'/projects/{project_id}/repository/compare',
            pat=pat,
            params={'from': source_branch, 'to': destination_branch}
        )

        added_files_to_destination = []
        deleted_files_from_source = []

        for diff in compare_data.get('diffs', []):
            if diff.get('new_file'):
                added_files_to_destination.append(diff['new_path'])
            elif diff.get('deleted_file'):
                deleted_files_from_source.append(diff['old_path'])
  

        return jsonify({
            "source_files": source_files,
            "destination_files": destination_files,
            "added_files_to_destination": sorted(added_files_to_destination),
            "deleted_files_from_source": sorted(deleted_files_from_source)
        })

    except Exception as e:
        print(f"An error occurred during file comparison: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
