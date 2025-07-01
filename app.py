
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import os
import subprocess
import shutil 
import re     
import sys   
from urllib.parse import urlparse, urlunparse

app = Flask(__name__)
CORS(app) 



REPO_BASE_DIR = os.path.join(os.getcwd(), 'cloned_repos')


os.makedirs(REPO_BASE_DIR, exist_ok=True)


CLONE_TIMEOUT_SECONDS = 600

FETCH_TIMEOUT_SECONDS = 120 

def sanitize_repo_name(url):
    """
    Sanitizes a repository URL to create a safe directory name.
    Removes protocol, slashes, and .git extension.
    """
    parsed_url = urlparse(url)
   
    if parsed_url.netloc:
        name = parsed_url.netloc + parsed_url.path
    else: 
        name = url.replace('git@', '').replace(':', '/')

    name = name.replace('.git', '')

    name = re.sub(r'[^a-zA-Z0-9_-]', '-', name)
  
    name = re.sub(r'-+', '-', name)
 
    name = name.strip('-')

    
    if not name or not re.search(r'[a-zA-Z0-9]', name):
        raise ValueError("Invalid repository URL provided, cannot create a safe directory name.")
    return name

def get_repo_path(repo_url):
    """Calculates the local path for a given repository URL."""
    try:
        repo_name = sanitize_repo_name(repo_url)
        return os.path.join(REPO_BASE_DIR, repo_name)
    except ValueError as e:
        raise e

def clone_or_fetch_repo(repo_url): 
   
    repo_path = "" 
    try:
        repo_path = get_repo_path(repo_url)
    except ValueError as e:
        raise Exception(f"Invalid repository URL: {e}")

 
    git_command_url = repo_url

    if not os.path.exists(repo_path):
        print(f"Attempting to clone {repo_url} into {repo_path} (Timeout: {CLONE_TIMEOUT_SECONDS}s)...")
        try:
            subprocess.run(
                ['git', 'clone', git_command_url, repo_path],
                check=True,
                capture_output=True,
                text=True,
                timeout=CLONE_TIMEOUT_SECONDS
            )
            print(f"Successfully cloned {repo_url}")
        except subprocess.TimeoutExpired:
            if os.path.exists(repo_path):
                shutil.rmtree(repo_path, ignore_errors=True)
            raise Exception(f"Git clone timed out after {CLONE_TIMEOUT_SECONDS} seconds for {repo_url}. "
                            "The repository might be too large or your network too slow.")
        except subprocess.CalledProcessError as e:
            error_output = e.stderr.strip()
            print(f"Error cloning repo: {error_output}")
            if "authentication" in error_output.lower() or "fatal: could not read Username" in error_output.lower():
                raise Exception(f"Authentication required or failed for {repo_url}. "
                                "Please ensure the repository is public or credentials are configured (e.g., SSH keys).")
            elif "repository not found" in error_output.lower() or "not a git repository" in error_output.lower():
                raise Exception(f"Repository not found or URL is incorrect: {repo_url}.")
            else:
                raise Exception(f"Failed to clone repository: {error_output}")
        except Exception as e:
            print(f"An unexpected error occurred during cloning: {e}")
            raise Exception(f"An unexpected error occurred during cloning: {str(e)}")
    else:
        print(f"Repository {repo_url} already exists. Fetching latest changes in {repo_path} (Timeout: {FETCH_TIMEOUT_SECONDS}s)...")
        try:
            subprocess.run(
                ['git', '-C', repo_path, 'fetch', 'origin'],
                check=True,
                capture_output=True,
                text=True,
                timeout=FETCH_TIMEOUT_SECONDS
            )
            print(f"Successfully fetched {repo_url}")
        except subprocess.TimeoutExpired:
            raise Exception(f"Git fetch timed out after {FETCH_TIMEOUT_SECONDS} seconds for {repo_url}. "
                            "Network might be slow or repository has many new changes.")
        except subprocess.CalledProcessError as e:
            error_output = e.stderr.strip()
            print(f"Error fetching repo: {error_output}")
            raise Exception(f"Failed to fetch repository updates: {error_output}")
        except Exception as e:
            print(f"An unexpected error occurred during fetching: {e}")
            raise Exception(f"An unexpected error occurred during fetching: {str(e)}")


@app.route('/')
def index():
    """Renders the main HTML page."""
    return render_template('index.html')

@app.route('/api/branches', methods=['POST'])
def get_branches():
    """
    API endpoint to fetch branches for a given GitLab repository URL.
    This will clone/fetch the repo and then list its branches.
    """
    data = request.get_json()
    repo_url = data.get('repoUrl')
  

    if not repo_url:
        return jsonify({"error": "Repository URL is required."}), 400

    try:
        clone_or_fetch_repo(repo_url) 
        repo_path = get_repo_path(repo_url)

        command = ['git', '-C', repo_path, 'branch', '-r']
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        lines = result.stdout.strip().split('\n')

        branches = []
        for line in lines:
            line = line.strip()
            if line.startswith('origin/'):
                branch_name = line.replace('origin/', '')
                if branch_name not in ['HEAD -> main', 'HEAD -> master', 'HEAD']:
                    branches.append(branch_name)
        
        branches = sorted(list(set(branches)))

        if 'main' in branches:
            branches.insert(0, branches.pop(branches.index('main')))
        elif 'master' in branches:
            branches.insert(0, branches.pop(branches.index('master')))
            
        return jsonify({"branches": branches})

    except Exception as e:
        print(f"Error in get_branches: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/diff', methods=['POST'])
def get_diff():
    """
    API endpoint to compare two branches of a given GitLab repository.
    This will ensure the repo is updated and then execute 'git diff'.
    """
    data = request.get_json()
    repo_url = data.get('repoUrl')
   
    branch1 = data.get('branch1')
    branch2 = data.get('branch2')

    if not all([repo_url, branch1, branch2]):
        return jsonify({"error": "Repository URL, branch1, and branch2 are required."}), 400

    try:
        clone_or_fetch_repo(repo_url)
        repo_path = get_repo_path(repo_url)

        print(f"Getting diff for origin/{branch1}..origin/{branch2} in {repo_path}")
        
        command = ['git', '-C', repo_path, 'diff', f'origin/{branch1}..origin/{branch2}']
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=FETCH_TIMEOUT_SECONDS
        )

        return jsonify({"diff": result.stdout})

    except subprocess.TimeoutExpired:
        raise Exception(f"Git diff command timed out after {FETCH_TIMEOUT_SECONDS} seconds. "
                        "This is unexpected; diffs are usually fast if repo is fetched.")
    except subprocess.CalledProcessError as e:
        error_output = e.stderr.strip()
        print(f"Git diff command failed: {error_output}")
        if "unknown revision" in error_output.lower() or "bad revision" in error_output.lower():
             return jsonify({"error": f"One or both branches ('{branch1}', '{branch2}') do not exist in the repository or are not accessible remotely. Please check branch names."}), 400
        return jsonify({"error": f"Failed to get diff: {error_output}"}), 500
    except Exception as e:
        print(f"Error in get_diff: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
