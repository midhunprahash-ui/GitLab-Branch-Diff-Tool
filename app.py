from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import re
from urllib.parse import urlparse
from configparser import ConfigParser
import gitlab # Import the python-gitlab library

app = Flask(__name__)
CORS(app)

# Load configuration
parser = ConfigParser()
parser.read(".config")

# --- Helper functions for GitLab API interactions ---

def parse_gitlab_url(repo_url):
    """
    Parse GitLab repository URL and extract base_url and project_path.
    Supports various GitLab URL formats:
    - https://gitlab.com/user/project
    - https://gitlab.com/user/project.git
    - git@gitlab.com:user/project.git
    - https://custom-gitlab.com/user/project
    """
    # Handle SSH URLs
    if repo_url.startswith('git@'):
        # git@gitlab.com:user/project.git -> gitlab.com/user/project
        match = re.match(r'git@([^:]+):(.+?)(?:\.git)?$', repo_url)
        if match:
            host, project_path = match.groups()
            base_url = f"https://{host}"
            return base_url, project_path.strip('/')
    
    # Handle HTTPS URLs
    parsed = urlparse(repo_url)
    if parsed.netloc and parsed.path:
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        project_path = parsed.path.strip('/').replace('.git', '')
        return base_url, project_path
    
    raise ValueError(f"Invalid GitLab repository URL: {repo_url}")

def get_gitlab_client(repo_url):
    """
    Initializes and returns a python-gitlab client instance.
    The GitLab base URL and access token are determined from the .config file and repo_url.
    """
    access_token = None
    if parser.has_option('default', 'GITLAB_TOKEN'):
        access_token = parser['default']['GITLAB_TOKEN']
    elif parser.has_option('default', 'PAT'):
        access_token = parser['default']['PAT']
    
    if not access_token:
        raise Exception("GitLab Personal Access Token (GITLAB_TOKEN or PAT) not found in .config file.")

    base_url, _ = parse_gitlab_url(repo_url)
    
    try:
        # Initialize the GitLab API client
        gl = gitlab.Gitlab(base_url, private_token=access_token, timeout=30)
        # Test authentication
        gl.auth()
        return gl
    except gitlab.exceptions.GitlabAuthenticationError as e:
        raise Exception(f"GitLab authentication failed for {base_url}. Check your PAT in .config. Error: {e}")
    except gitlab.exceptions.GitlabError as e:
        raise Exception(f"Failed to connect to GitLab instance at {base_url}. Error: {e}")
    except Exception as e:
        raise Exception(f"An unexpected error occurred initializing GitLab client: {str(e)}")

# --- Flask Routes ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/branches', methods=['POST'])
def get_branches():
    data = request.get_json()
    repo_url = data.get('repoUrl')
    
    if not repo_url:
        return jsonify({"error": "Repository URL is required."}), 400
    
    try:
        gl = get_gitlab_client(repo_url)
        _, project_path = parse_gitlab_url(repo_url)

        # Get project by path (which is URL-encoded by python-gitlab automatically)
        project = gl.projects.get(project_path, lazy=True) # lazy=True fetches only essential info first

        # Get branches
        # per_page can be increased for large repos, or pagination used
        branches_data = project.branches.list(per_page=100) 
        
        branches = [branch.name for branch in branches_data]
        
        branches = sorted(list(set(branches)))
        if 'main' in branches:
            branches.insert(0, branches.pop(branches.index('main')))
        elif 'master' in branches:
            branches.insert(0, branches.pop(branches.index('master')))
            
        return jsonify({"branches": branches})
        
    except gitlab.exceptions.GitlabError as e:
        error_msg = f"GitLab API error: {e}"
        if e.response_code == 404:
            error_msg = f"Project not found at '{project_path}' or you don't have access. Please check the repository URL and your PAT."
        elif e.response_code == 401:
            error_msg = "Authentication failed. Please check your GitLab Personal Access Token in the .config file."
        elif e.response_code == 403:
            error_msg = "Access denied. You don't have permission to access this repository."
        print(f"Error in get_branches: {error_msg}")
        return jsonify({"error": error_msg}), e.response_code if hasattr(e, 'response_code') else 500
    except Exception as e:
        print(f"Error in get_branches: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/diff', methods=['POST'])
def get_diff():
    data = request.get_json()
    repo_url = data.get('repoUrl')
    branch1 = data.get('branch1')
    branch2 = data.get('branch2')
    
    if not all([repo_url, branch1, branch2]):
        return jsonify({"error": "Repository URL, branch1, and branch2 are required."}), 400
    
    try:
        gl = get_gitlab_client(repo_url)
        _, project_path = parse_gitlab_url(repo_url)
        project = gl.projects.get(project_path, lazy=True)

        # Use the compare API
        # The 'straight' parameter ensures a direct diff between the two commits/branches,
        # not necessarily based on a common merge base, mimicking `git diff branch1..branch2`
        compare_data = project.repository_compare(branch1, branch2, straight=True)
        
        diff_output = ""
        if 'diffs' in compare_data:
            for diff in compare_data['diffs']:
                diff_output += f"diff --git a/{diff['old_path']} b/{diff['new_path']}\n"
                if diff.get('new_file'):
                    diff_output += f"new file mode {diff.get('b_mode', '100644')}\n"
                elif diff.get('deleted_file'):
                    diff_output += f"deleted file mode {diff.get('a_mode', '100644')}\n"
                elif diff.get('renamed_file'):
                    diff_output += f"similarity index {diff.get('similarity_index', 0)}%\n"
                    diff_output += f"rename from {diff['old_path']}\n"
                    diff_output += f"rename to {diff['new_path']}\n"
                
                if 'diff' in diff:
                    diff_output += diff['diff'] + "\n"
        else:
            diff_output = "No differences found between the branches."
                
        return jsonify({"diff": diff_output})
        
    except gitlab.exceptions.GitlabError as e:
        error_msg = f"GitLab API error: {e}"
        if e.response_code == 400:
            error_msg = f"Invalid branch names: '{branch1}' or '{branch2}' may not exist in '{project_path}'."
        elif e.response_code == 404:
            error_msg = f"Comparison failed. Project '{project_path}' not found, or branches '{branch1}', '{branch2}' do not exist."
        print(f"Error in get_diff: {error_msg}")
        return jsonify({"error": error_msg}), e.response_code if hasattr(e, 'response_code') else 500
    except Exception as e:
        print(f"Error in get_diff: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/file_content', methods=['POST'])
def get_file_content():
    data = request.get_json()
    repo_url = data.get('repoUrl')
    file_path = data.get('filePath')
    ref = data.get('ref') # branch name or commit SHA
    
    if not all([repo_url, file_path, ref]):
        return jsonify({"error": "Repository URL, file path, and reference (branch/commit) are required."}), 400
    
    try:
        gl = get_gitlab_client(repo_url)
        _, project_path = parse_gitlab_url(repo_url)
        project = gl.projects.get(project_path, lazy=True)

        # Get file content
        file_obj = project.files.get(file_path=file_path, ref=ref)
        # The content is base64 encoded, decode it
        decoded_content = file_obj.decode().decode('utf-8')
        
        return jsonify({"content": decoded_content})
        
    except gitlab.exceptions.GitlabError as e:
        error_msg = f"GitLab API error: {e}"
        if e.response_code == 404:
            error_msg = f"File '{file_path}' not found in branch/ref '{ref}' of project '{project_path}', or access denied."
        elif e.response_code == 400:
             error_msg = f"Invalid file path or reference provided for project '{project_path}'."
        print(f"Error in get_file_content: {error_msg}")
        return jsonify({"error": error_msg}), e.response_code if hasattr(e, 'response_code') else 500
    except Exception as e:
        print(f"Error in get_file_content: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/repo_details', methods=['POST'])
def get_repo_details():
    data = request.get_json()
    repo_url = data.get('repoUrl')
    
    if not repo_url:
        return jsonify({"error": "Repository URL is required."}), 400
    
    try:
        gl = get_gitlab_client(repo_url)
        _, project_path = parse_gitlab_url(repo_url)
        
        # Get project details. This fetches the full project object.
        project_details = gl.projects.get(project_path, statistics=True) 
        
        # Convert the GitLab project object to a dictionary for JSON serialization
        details_dict = project_details.asdict()
        
        return jsonify(details_dict)
        
    except gitlab.exceptions.GitlabError as e:
        error_msg = f"GitLab API error: {e}"
        if e.response_code == 404:
            error_msg = f"Project not found at '{project_path}' or you don't have access. Please check the repository URL and your PAT."
        elif e.response_code == 401:
            error_msg = "Authentication failed. Please check your GitLab Personal Access Token in the .config file."
        elif e.response_code == 403:
            error_msg = "Access denied. You don't have permission to access this repository."
        print(f"Error in get_repo_details: {error_msg}")
        return jsonify({"error": error_msg}), e.response_code if hasattr(e, 'response_code') else 500
    except Exception as e:
        print(f"Error in get_repo_details: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')