from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
from urllib.parse import quote, urlparse
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)


def get_gitlab_base_url(repo_url):
    parsed_url = urlparse(repo_url)
    if not parsed_url.scheme or not parsed_url.netloc:
        raise ValueError("Invalid GitLab repository URL. Must include scheme (e.g., https://) and hostname.")
    return f"{parsed_url.scheme}://{parsed_url.netloc}"

def get_project_id_from_url(repo_url):
    parsed_url = urlparse(repo_url)
    path_parts = [part for part in parsed_url.path.split('/') if part]

    if not path_parts:
        raise ValueError("Invalid GitLab repository URL format. Could not extract project path.")

    project_path = '/'.join(path_parts)
    if project_path.endswith('.git'):
        project_path = project_path[:-4]

    return quote(project_path, safe='')


def make_gitlab_api_request(endpoint, base_url, pat=None, params=None):
    headers = {}
    if pat:
        headers['Private-Token'] = pat

    all_results = []
    page = 1
    per_page = 100

    while True:
        current_params = params.copy() if params else {}
        current_params['page'] = page
        current_params['per_page'] = per_page

        url = f"{base_url}/api/v4{endpoint}"
        print(f"Making GitLab API request to: {url} with params: {current_params} (PAT provided: {bool(pat)})")

        try:
            response = requests.get(url, headers=headers, params=current_params, timeout=30)
            response.raise_for_status()

            page_results = response.json()

            if not isinstance(page_results, list):
                all_results.append(page_results)
                break

            if not page_results:
                break

            all_results.extend(page_results)

            if len(page_results) < per_page:
                break
            page += 1

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

    return all_results

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/branches', methods=['POST'])
def get_branches():
    data = request.get_json()
    repo_url = data.get('repoUrl')
    pat = data.get('pat')

    print(f"Received request for branches: repoUrl={repo_url}, PAT provided={bool(pat)}")

    if not repo_url:
        return jsonify({"error": "Repository URL is required."}), 400

    try:
        base_url = get_gitlab_base_url(repo_url)
        project_id = get_project_id_from_url(repo_url)
        branches_data = make_gitlab_api_request(
            f'/projects/{project_id}/repository/branches',
            base_url=base_url,
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
    data = request.get_json()
    repo_url = data.get('repoUrl')
    pat = data.get('pat')
    source_branch = data.get('sourceBranch')
    destination_branch = data.get('destinationBranch')
    from_date_str = data.get('fromDate')
    to_date_str = data.get('toDate')

    print(f"Received request for comparing commits: repoUrl={repo_url}, sourceBranch={source_branch}, destinationBranch={destination_branch}, fromDate={from_date_str}, toDate={to_date_str}, PAT provided={bool(pat)}")

    if not all([repo_url, source_branch, destination_branch]):
        return jsonify({"error": "Repository URL, sourceBranch, and destinationBranch are required."}), 400

    params = {}
    if from_date_str:
        params['since'] = f"{from_date_str}T00:00:00Z"
    if to_date_str:
        params['until'] = f"{to_date_str}T23:59:59Z"

    try:
        base_url = get_gitlab_base_url(repo_url)
        project_id = get_project_id_from_url(repo_url)

        source_commits_data = make_gitlab_api_request(
            f'/projects/{project_id}/repository/commits',
            base_url=base_url,
            pat=pat,
            params={**params, 'ref_name': source_branch}
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
            base_url=base_url,
            pat=pat,
            params={**params, 'ref_name': destination_branch}
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
    data = request.json
    repo_url = data.get('repoUrl')
    pat = data.get('pat')
    source_branch = data.get('sourceBranch')
    destination_branch = data.get('destinationBranch')

    print(f"Received request for file comparison: repoUrl={repo_url}, sourceBranch={source_branch}, destinationBranch={destination_branch}, PAT provided={bool(pat)}")

    if not all([repo_url, source_branch, destination_branch]):
        return jsonify({"error": "Missing repository URL, source branch, or destination branch"}), 400

    try:
        base_url = get_gitlab_base_url(repo_url)
        project_id = get_project_id_from_url(repo_url)

        source_branch_info = make_gitlab_api_request(
            f'/projects/{project_id}/repository/branches/{quote(source_branch, safe="")}',
            base_url=base_url,
            pat=pat
        )

        source_commit_id = source_branch_info[0]['commit']['id'] if isinstance(source_branch_info, list) else source_branch_info['commit']['id']

        source_tree_data = make_gitlab_api_request(
            f'/projects/{project_id}/repository/tree',
            base_url=base_url,
            pat=pat,
            params={'ref': source_commit_id, 'recursive': True}
        )
        source_files = sorted([item['path'] for item in source_tree_data if item['type'] == 'blob'])
        print(f"DEBUG: Files in source branch ({source_branch}): {source_files}")
        print(f"Found {len(source_files)} files in source branch.")


        destination_branch_info = make_gitlab_api_request(
            f'/projects/{project_id}/repository/branches/{quote(destination_branch, safe="")}',
            base_url=base_url,
            pat=pat
        )

        destination_commit_id = destination_branch_info[0]['commit']['id'] if isinstance(destination_branch_info, list) else destination_branch_info['commit']['id']

        destination_tree_data = make_gitlab_api_request(
            f'/projects/{project_id}/repository/tree',
            base_url=base_url,
            pat=pat,
            params={'ref': destination_commit_id, 'recursive': True}
        )
        destination_files = sorted([item['path'] for item in destination_tree_data if item['type'] == 'blob'])
        print(f"DEBUG: Files in destination branch ({destination_branch}): {destination_files}")
        print(f"Found {len(destination_files)} files in destination branch.")


        source_files_set = set(source_files)
        destination_files_set = set(destination_files)


        added_files_to_destination = sorted(list(destination_files_set - source_files_set))

        deleted_files_from_source = sorted(list(source_files_set - destination_files_set))

        modified_files = []
        try:
            compare_data = make_gitlab_api_request(
                f'/projects/{project_id}/repository/compare',
                base_url=base_url,
                pat=pat,
                params={'from': source_branch, 'to': destination_branch, 'straight': True}
            )

            compare_diffs = compare_data[0].get('diffs', []) if compare_data else []
            print(f"Raw GitLab Compare Diffs: {compare_diffs}")

            for diff_obj in compare_diffs:
                if not diff_obj.get('new_file') and not diff_obj.get('deleted_file') and diff_obj.get('diff'):
                    modified_files.append(diff_obj.get('new_path') or diff_obj.get('old_path'))
            modified_files = sorted(list(set(modified_files)))
        except Exception as e:
            print(f"Warning: Could not fetch diffs for modified files: {e}")


        print(f"Backend Final Lists - Added: {added_files_to_destination}, Deleted: {deleted_files_from_source}, Modified: {modified_files}")


        return jsonify({
            "source_files": source_files,
            "destination_files": destination_files,
            "added_files_to_destination": added_files_to_destination,
            "deleted_files_from_source": deleted_files_from_source,
            "modified_files": modified_files
        })

    except Exception as e:
        print(f"An error occurred during file comparison: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/file_content_diff', methods=['POST'])
def get_file_content_diff():
    data = request.json
    repo_url = data.get('repoUrl')
    pat = data.get('pat')
    source_branch = data.get('sourceBranch')
    destination_branch = data.get('destinationBranch')
    file_path = data.get('filePath')

    print(f"Received request for file content diff: repoUrl={repo_url}, sourceBranch={source_branch}, destinationBranch={destination_branch}, filePath={file_path}, PAT provided={bool(pat)})")

    if not all([repo_url, source_branch, destination_branch, file_path]):
        return jsonify({"error": "Missing required parameters for file content diff."}), 400

    try:
        base_url = get_gitlab_base_url(repo_url)
        project_id = get_project_id_from_url(repo_url)

        compare_data = make_gitlab_api_request(
            f'/projects/{project_id}/repository/compare',
            base_url=base_url,
            pat=pat,
            params={'from': source_branch, 'to': destination_branch, 'straight': True}
        )

        compare_diffs = compare_data[0].get('diffs', []) if compare_data else []

        file_diff_content = None
        for diff_obj in compare_diffs:
            if diff_obj.get('old_path') == file_path or diff_obj.get('new_path') == file_path:
                file_diff_content = diff_obj.get('diff', '')
                break

        if file_diff_content is None:
            return jsonify({"diff_content": "", "message": "File content is identical or file not found in diff."})

        return jsonify({"diff_content": file_diff_content})

    except Exception as e:
        print(f"Error fetching file content diff: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')