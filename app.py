from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
import re
from urllib.parse import urlparse
from configparser import ConfigParser
import base64

app = Flask(__name__)
CORS(app)

# Load configuration
parser = ConfigParser()
parser.read(".config")

class GitLabAPI:
    def __init__(self, base_url="https://gitlab.com", access_token=None):
        self.base_url = base_url.rstrip('/')
        self.access_token = access_token
        self.headers = {
            'Authorization': f'Bearer {access_token}' if access_token else None,
            'Content-Type': 'application/json'
        }
        # Remove None values from headers
        self.headers = {k: v for k, v in self.headers.items() if v is not None}
    
    def parse_gitlab_url(self, repo_url):
        """
        Parse GitLab repository URL and extract project path
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
                return base_url, project_path
        
        # Handle HTTPS URLs
        parsed = urlparse(repo_url)
        if parsed.netloc and parsed.path:
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            project_path = parsed.path.strip('/').replace('.git', '')
            return base_url, project_path
        
        raise ValueError(f"Invalid GitLab repository URL: {repo_url}")
    
    def get_project_id(self, project_path):
        """Get project ID from project path"""
        # URL encode the project path for API calls
        encoded_path = requests.utils.quote(project_path, safe='')
        url = f"{self.base_url}/api/v4/projects/{encoded_path}"
        
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()['id']
        except requests.exceptions.RequestException as e:
            if response.status_code == 404:
                raise Exception(f"Project not found: {project_path}. Please check if the repository exists and is accessible.")
            elif response.status_code == 401:
                raise Exception("Authentication failed. Please check your GitLab access token.")
            elif response.status_code == 403:
                raise Exception("Access denied. You don't have permission to access this repository.")
            else:
                raise Exception(f"Failed to get project information: {str(e)}")
    
    def get_branches(self, project_id):
        """Get all branches for a project"""
        url = f"{self.base_url}/api/v4/projects/{project_id}/repository/branches"
        
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            branches_data = response.json()
            
            # Extract branch names
            branches = [branch['name'] for branch in branches_data]
            
            # Sort branches with main/master first
            branches = sorted(list(set(branches)))
            if 'main' in branches:
                branches.insert(0, branches.pop(branches.index('main')))
            elif 'master' in branches:
                branches.insert(0, branches.pop(branches.index('master')))
            
            return branches
            
        except requests.exceptions.RequestException as e:
            if hasattr(e, 'response') and e.response.status_code == 404:
                raise Exception("Repository branches not found or not accessible.")
            else:
                raise Exception(f"Failed to fetch branches: {str(e)}")
    
    def get_compare_diff(self, project_id, from_branch, to_branch):
        """Get diff between two branches"""
        url = f"{self.base_url}/api/v4/projects/{project_id}/repository/compare"
        params = {
            'from': from_branch,
            'to': to_branch,
            'straight': 'true'  # Get direct comparison, not merge-base
        }
        
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=60)
            response.raise_for_status()
            compare_data = response.json()
            
            # Get the diff from the response
            if 'diffs' in compare_data:
                # Format the diff similar to git diff output
                diff_output = ""
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
                
                return diff_output
            else:
                return "No differences found between the branches."
                
        except requests.exceptions.RequestException as e:
            if hasattr(e, 'response'):
                if e.response.status_code == 400:
                    raise Exception(f"Invalid branch names: '{from_branch}' or '{to_branch}' may not exist.")
                elif e.response.status_code == 404:
                    raise Exception("Comparison not found. Please check if both branches exist.")
            raise Exception(f"Failed to get diff: {str(e)}")

def get_gitlab_client(repo_url):
    """Create GitLab API client based on repository URL"""
    try:
        # Get access token from config
        access_token = None
        if parser.has_option('default', 'GITLAB_TOKEN'):
            access_token = parser['default']['GITLAB_TOKEN']
        elif parser.has_option('default', 'PAT'):  # Fallback to existing PAT
            access_token = parser['default']['PAT']
        
        # Parse URL to get GitLab instance
        if repo_url.startswith('git@'):
            match = re.match(r'git@([^:]+):', repo_url)
            if match:
                base_url = f"https://{match.group(1)}"
            else:
                base_url = "https://gitlab.com"
        else:
            parsed = urlparse(repo_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
        
        return GitLabAPI(base_url=base_url, access_token=access_token)
        
    except Exception as e:
        raise Exception(f"Failed to initialize GitLab client: {str(e)}")

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
        # Initialize GitLab API client
        gitlab_client = get_gitlab_client(repo_url)
        
        # Parse repository URL
        base_url, project_path = gitlab_client.parse_gitlab_url(repo_url)
        
        # Update client base URL if different
        if gitlab_client.base_url != base_url:
            gitlab_client.base_url = base_url
        
        # Get project ID
        project_id = gitlab_client.get_project_id(project_path)
        
        # Get branches
        branches = gitlab_client.get_branches(project_id)
        
        return jsonify({"branches": branches})
        
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
        # Initialize GitLab API client
        gitlab_client = get_gitlab_client(repo_url)
        
        # Parse repository URL
        base_url, project_path = gitlab_client.parse_gitlab_url(repo_url)
        
        # Update client base URL if different
        if gitlab_client.base_url != base_url:
            gitlab_client.base_url = base_url
        
        # Get project ID
        project_id = gitlab_client.get_project_id(project_path)
        
        # Get diff between branches
        diff_output = gitlab_client.get_compare_diff(project_id, branch1, branch2)
        
        return jsonify({"diff": diff_output})
        
    except Exception as e:
        print(f"Error in get_diff: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')