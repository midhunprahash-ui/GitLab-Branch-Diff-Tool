# app.py
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS # Import CORS
import gitlab
import os

app = Flask(__name__)
CORS(app) # Enable CORS for all origins

def get_gitlab_instance(gitlab_url, pat):
    """
    Helper function to initialize the python-gitlab instance.
    Handles common URL formatting issues and tests authentication.
    """
    # Ensure the GitLab URL is correctly formatted for python-gitlab
    # It should not end with /api/v4
    if gitlab_url.endswith('/api/v4'):
        gitlab_url = gitlab_url.replace('/api/v4', '').rstrip('/')
    elif gitlab_url.endswith('/'):
        gitlab_url = gitlab_url.rstrip('/')

    try:
        gl = gitlab.Gitlab(gitlab_url, private_token=pat)
        gl.auth() # Test authentication
        return gl
    except gitlab.exceptions.GitlabError as e:
        # Log the error for debugging purposes (optional in production, but useful here)
        # app.logger.error(f"GitLab authentication error: {e}")
        raise e # Re-raise to be caught by the calling route
    except Exception as e:
        # app.logger.error(f"An unexpected error occurred during GitLab client initialization: {e}")
        raise e

@app.route('/')
def index():
    """Renders the main index page."""
    return render_template('index.html')

@app.route('/get_projects', methods=['POST'])
def get_projects():
    """
    Fetches and returns a list of projects from GitLab for the given user
    using python-gitlab.
    """
    gitlab_url = request.form.get('gitlab_url')
    pat = request.form.get('pat')

    if not gitlab_url or not pat:
        return jsonify({'error': 'GitLab URL and Personal Access Token are required.'}), 400

    try:
        gl = get_gitlab_instance(gitlab_url, pat)

        # Fetch all projects the user is a member of.
        # all=True handles pagination automatically.
        projects = gl.projects.list(all=True, membership=True, archived=False)

        projects_data = []
        for project in projects:
            projects_data.append({
                'id': project.id,
                'name': project.name_with_namespace,
                'web_url': project.web_url,
                'path_with_namespace': project.path_with_namespace # Useful for display or later direct access
            })

        return jsonify({'projects': projects_data})

    except gitlab.exceptions.GitlabError as e:
        # Catch specific GitLab API errors (e.g., 401 Unauthorized, 403 Forbidden)
        return jsonify({'error': f'GitLab API Error: {e}'}), e.response_code if hasattr(e, 'response_code') else 500
    except Exception as e:
        # Catch any other unexpected errors
        return jsonify({'error': f'An unexpected error occurred: {e}'}), 500

@app.route('/get_branches', methods=['POST'])
def get_branches():
    """
    Fetches and returns a list of branches for a given project ID.
    """
    data = request.get_json()
    project_id = data.get('project_id')
    gitlab_url = data.get('gitlab_url')
    pat = data.get('pat')

    if not project_id or not gitlab_url or not pat:
        return jsonify({'error': 'Project ID, GitLab URL, and PAT are required.'}), 400

    try:
        gl = get_gitlab_instance(gitlab_url, pat)
        project = gl.projects.get(project_id) # Get the specific project object

        # Fetch all branches for the project
        branches = project.branches.list(all=True)

        branches_data = []
        for branch in branches:
            branches_data.append({
                'name': branch.name,
                'commit_id': branch.commit['id'] # Get the latest commit ID for the branch
            })

        return jsonify({'branches': branches_data})

    except gitlab.exceptions.GitlabError as e:
        return jsonify({'error': f'GitLab API Error: {e}'}), e.response_code if hasattr(e, 'response_code') else 500
    except Exception as e:
        return jsonify({'error': f'An unexpected error occurred: {e}'}), 500

@app.route('/compare_branches', methods=['POST'])
def compare_branches():
    """
    Compares two branches of a project and returns the commit differences.
    """
    data = request.get_json()
    project_id = data.get('project_id')
    source_branch = data.get('source_branch')
    target_branch = data.get('target_branch')
    gitlab_url = data.get('gitlab_url')
    pat = data.get('pat')

    if not all([project_id, source_branch, target_branch, gitlab_url, pat]):
        return jsonify({'error': 'All comparison parameters are required.'}), 400

    try:
        gl = get_gitlab_instance(gitlab_url, pat)
        project = gl.projects.get(project_id)

        # Use the repository.compare method to get the comparison details
        # The 'from' and 'to' arguments refer to the branch names or commit SHAs
        comparison = project.repository_compare(source_branch, target_branch)

        commits_data = []
        # The comparison object contains a list of commits that are unique to the 'to' branch
        # relative to the 'from' branch.
        for commit in comparison.commits:
            commits_data.append({
                'id': commit['id'],
                'short_id': commit['short_id'],
                'message': commit['message'],
                'author_name': commit['author_name'],
                'authored_date': commit['authored_date']
            })
        
        return jsonify({'commits': commits_data})

    except gitlab.exceptions.GitlabError as e:
        return jsonify({'error': f'GitLab API Error: {e}'}), e.response_code if hasattr(e, 'response_code') else 500
    except Exception as e:
        return jsonify({'error': f'An unexpected error occurred: {e}'}), 500

@app.route('/get_diff', methods=['POST'])
def get_diff():
    """
    Fetches the raw diff content for a specific commit ID.
    """
    data = request.get_json()
    project_id = data.get('project_id')
    commit_id = data.get('commit_id')
    gitlab_url = data.get('gitlab_url')
    pat = data.get('pat')

    if not all([project_id, commit_id, gitlab_url, pat]):
        return jsonify({'error': 'Project ID, Commit ID, GitLab URL, and PAT are required.'}), 400

    try:
        gl = get_gitlab_instance(gitlab_url, pat)
        project = gl.projects.get(project_id)

        # Get the commit object
        commit = project.commits.get(commit_id)

        # Fetch the diff for the commit
        # The diff() method returns a list of diff objects, each representing a file change.
        # We need to concatenate the 'diff' attribute from each object.
        diffs = commit.diff()
        full_diff_text = ""
        for d in diffs:
            # Each diff object has a 'diff' key containing the actual diff string for that file
            full_diff_text += d.get('diff', '') + "\n" # Add a newline between file diffs

        return jsonify({'diff': full_diff_text})

    except gitlab.exceptions.GitlabError as e:
        return jsonify({'error': f'GitLab API Error: {e}'}), e.response_code if hasattr(e, 'response_code') else 500
    except Exception as e:
        return jsonify({'error': f'An unexpected error occurred: {e}'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
