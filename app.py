from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import gitlab
import re # Import regex for URL parsing

app = Flask(__name__)
CORS(app) # Enable CORS for all routes (adjust as needed for security)

# --- Routes for your HTML Frontend ---
@app.route('/')
def index():
    return render_template('index.html')

# --- API Endpoints ---

@app.route('/api/get_repos', methods=['POST'])
def get_repos():
    data = request.json
    raw_gitlab_url = data.get('gitlab_url')
    pat = data.get('pat')

    if not raw_gitlab_url or not pat:
        return jsonify({'error': 'GitLab URL and Personal Access Token are required.'}), 400

    # Determine the actual GitLab base URL from the input.
    # If a profile URL like gitlab.com/username is given, use gitlab.com
    # Otherwise, use the provided URL directly.
    gitlab_base_url = raw_gitlab_url
    if re.match(r'^https?:\/\/gitlab\.com\/[a-zA-Z0-9_-]+$', raw_gitlab_url):
        gitlab_base_url = 'https://gitlab.com'
    elif re.match(r'^https?:\/\/gitlab\.com$', raw_gitlab_url):
        gitlab_base_url = 'https://gitlab.com'
    # Add more specific checks if you expect other internal URLs like gitlab.organization.com

    try:
        # Initialize GitLab API client
        gl = gitlab.Gitlab(gitlab_base_url, private_token=pat)
        gl.auth() # Authenticate with GitLab

        # Fetch projects owned by the user whose PAT is provided
        # This will list both public and private projects accessible by this PAT.
        projects = gl.projects.list(owned=True, all=True)

        repo_list = []
        for p in projects:
            repo_list.append({
                'id': p.id,
                'name': p.name,
                'path_with_namespace': p.path_with_namespace,
                'web_url': p.web_url,
                'visibility': p.visibility
            })
        return jsonify(repo_list)

    except gitlab.exceptions.GitlabAuthenticationError:
        return jsonify({'error': 'Authentication failed. Check your PAT and ensure it has the correct scopes (e.g., api or read_api).'}), 401
    except gitlab.exceptions.GitlabError as e:
        # This will now include the Cloudflare HTML if that's the error
        print(f"GitLab API Error in get_repos: {e.response_code} - {e.response_content.decode('utf-8')}") # Log full response for debugging
        return jsonify({'error': f'GitLab API error: {e.response_code}: {e.error_message or e.response_content.decode("utf-8")[:200]}...'}), 500
    except Exception as e:
        print(f"An unexpected error occurred in get_repos: {str(e)}") # Log unexpected errors
        return jsonify({'error': f'An unexpected error occurred: {str(e)}'}), 500

@app.route('/api/get_branches/<int:project_id>', methods=['POST'])
def get_branches(project_id):
    data = request.json
    raw_gitlab_url = data.get('gitlab_url')
    pat = data.get('pat')

    if not raw_gitlab_url or not pat:
        return jsonify({'error': 'GitLab URL and PAT are required.'}), 400

    gitlab_base_url = raw_gitlab_url
    if re.match(r'^https?:\/\/gitlab\.com\/[a-zA-Z0-9_-]+$', raw_gitlab_url):
        gitlab_base_url = 'https://gitlab.com'
    elif re.match(r'^https?:\/\/gitlab\.com$', raw_gitlab_url):
        gitlab_base_url = 'https://gitlab.com'

    try:
        gl = gitlab.Gitlab(gitlab_base_url, private_token=pat)
        gl.auth()

        # --- DEBUGGING PRINTS ---
        print(f"\n--- Debugging get_branches (Project ID: {project_id}) ---")
        print(f"GitLab Base URL: {gitlab_base_url}")
        print(f"PAT provided: {'Yes' if pat else 'No'}")
        # --- END DEBUGGING PRINTS ---

        project = gl.projects.get(project_id)

        # --- DEBUGGING PRINTS ---
        print(f"Type of object returned by gl.projects.get: {type(project)}")
        print(f"Does the 'project' object have 'repository' attribute? {'repository' in dir(project)}")
        if hasattr(project, 'name'): # Check if it has basic project attributes
            print(f"Project Name: {project.name}")
        else:
            print("Project object seems malformed (no name attribute).")
        # --- END DEBUGGING PRINTS ---

        branches = project.branches.list(all=True) # This is where the error might occur

        branch_names = [b.name for b in branches]
        return jsonify(branch_names)

    except gitlab.exceptions.GitlabAuthenticationError:
        return jsonify({'error': 'Authentication failed. Check your PAT.'}), 401
    except gitlab.exceptions.GitlabError as e:
        print(f"GitLab API Error in get_branches: {e.response_code} - {e.response_content.decode('utf-8')}")
        return jsonify({'error': f'GitLab API error: {e.response_code}: {e.error_message or e.response_content.decode("utf-8")[:200]}...'}), 500
    except AttributeError as e:
        # Catch the specific AttributeError here to provide more context
        print(f"AttributeError in get_branches: {str(e)}")
        return jsonify({'error': f"An attribute error occurred in get_branches, likely with the project object: {str(e)}. Please check Flask console for details."}), 500
    except Exception as e:
        print(f"An unexpected error occurred in get_branches: {str(e)}")
        return jsonify({'error': f'An unexpected error occurred: {str(e)}'}), 500

@app.route('/api/compare_branches/<int:project_id>', methods=['POST'])
def compare_branches(project_id):
    data = request.json
    raw_gitlab_url = data.get('gitlab_url')
    pat = data.get('pat')
    from_branch = data.get('from_branch')
    to_branch = data.get('to_branch')

    if not raw_gitlab_url or not pat or not from_branch or not to_branch:
        return jsonify({'error': 'GitLab URL, PAT, from_branch, and to_branch are required.'}), 400

    gitlab_base_url = raw_gitlab_url
    if re.match(r'^https?:\/\/gitlab\.com\/[a-zA-Z0-9_-]+$', raw_gitlab_url):
        gitlab_base_url = 'https://gitlab.com'
    elif re.match(r'^https?:\/\/gitlab\.com$', raw_gitlab_url):
        gitlab_base_url = 'https://gitlab.com'

    try:
        gl = gitlab.Gitlab(gitlab_base_url, private_token=pat)
        gl.auth()

        # --- DEBUGGING PRINTS ---
        print(f"\n--- Debugging compare_branches (Project ID: {project_id}) ---")
        print(f"GitLab Base URL: {gitlab_base_url}")
        print(f"PAT provided: {'Yes' if pat else 'No'}")
        print(f"From Branch: {from_branch}, To Branch: {to_branch}")
        # --- END DEBUGGING PRINTS ---

        project = gl.projects.get(project_id)

        # --- DEBUGGING PRINTS ---
        print(f"Type of object returned by gl.projects.get: {type(project)}")
        print(f"Does the 'project' object have 'repository' attribute? {'repository' in dir(project)}")
        if hasattr(project, 'name'):
            print(f"Project Name: {project.name}")
        else:
            print("Project object seems malformed (no name attribute).")
        # --- END DEBUGGING PRINTS ---

        comparison = project.repository.compare(from_branch, to_branch) # This is where the error might occur

        commits_info = [{
            'id': c['id'],
            'short_id': c['short_id'],
            'title': c['title'],
            'author_name': c['author_name'],
            'authored_date': c['authored_date']
        } for c in comparison['commits']]

        diffs_info = [{
            'old_path': d['old_path'],
            'new_path': d['new_path'],
            'diff': d['diff']
        } for d in comparison['diffs']]

        return jsonify({
            'commits': commits_info,
            'diffs': diffs_info
        })

    except gitlab.exceptions.GitlabAuthenticationError:
        return jsonify({'error': 'Authentication failed. Check your PAT.'}), 401
    except gitlab.exceptions.GitlabError as e:
        print(f"GitLab API Error in compare_branches: {e.response_code} - {e.response_content.decode('utf-8')}")
        return jsonify({'error': f'GitLab API error: {e.response_code}: {e.error_message or e.response_content.decode("utf-8")[:200]}...'}), 500
    except AttributeError as e:
        # Catch the specific AttributeError here to provide more context
        print(f"AttributeError in compare_branches: {str(e)}")
        return jsonify({'error': f"An attribute error occurred in compare_branches, likely with the project object: {str(e)}. Please check Flask console for details."}), 500
    except Exception as e:
        print(f"An unexpected error occurred in compare_branches: {str(e)}")
        return jsonify({'error': f'An unexpected error occurred: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)