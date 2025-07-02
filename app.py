from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import subprocess
import re
import sys
from urllib.parse import urlparse, urlunparse
import os # Added for os.path.exists and os.makedirs (though not used for persistent clones now)
import tempfile # Added for temporary directory creation
import shutil 

app = Flask(__name__)
CORS(app)

# Global constants for timeouts
CLONE_TIMEOUT_SECONDS = 600 # Used for the shallow temporary clone
FETCH_TIMEOUT_SECONDS = 120 # Used for fetching specific branches into the temp clone and ls-remote

def construct_git_url_with_pat(repo_url, username, pat):
    """
    Constructs the Git URL with username and PAT for authentication.
    Handles both HTTPS and SSH URLs, converting SSH to HTTPS if a PAT is provided.
    """
    parsed_url = urlparse(repo_url)

    # If the URL is already in a format that includes credentials, or no PAT is provided,
    # return it as is. This prevents double-injection.
    if pat and parsed_url.netloc and f"{username}:{pat}@" in parsed_url.netloc:
        print(f"PAT already seems to be in URL or no PAT provided. Returning original URL.")
        return repo_url
    
    if parsed_url.scheme == 'https':
        # Inject username and PAT into HTTPS URL
        # Remove existing credentials if any, to ensure clean injection
        clean_netloc = parsed_url.netloc
        if '@' in clean_netloc:
            clean_netloc = clean_netloc.split('@', 1)[-1] # Get part after @
        
        new_netloc = f"{username}:{pat}@{clean_netloc}"
        
        # Reconstruct the URL
        authenticated_url = urlunparse(parsed_url._replace(netloc=new_netloc))
        print(f"Constructed HTTPS URL with PAT: {authenticated_url[:authenticated_url.find('@') + 1]}... (hidden PAT)")
        return authenticated_url
    elif parsed_url.scheme == '' and parsed_url.netloc == '':
        # Likely an SSH URL format like git@gitlab.com:user/repo.git
        if 'git@' in repo_url:
            # Convert SSH to HTTPS for PAT authentication
            ssh_parts = repo_url.split('git@', 1)
            if len(ssh_parts) < 2:
                raise ValueError("Invalid SSH repository URL format.")
            
            ssh_path = ssh_parts[1]
            if ':' not in ssh_path:
                raise ValueError("Invalid SSH repository URL format: missing colon for path.")

            host, path = ssh_path.split(':', 1)
            
            # Ensure path starts with a slash for HTTPS URL
            if not path.startswith('/'):
                path = '/' + path
            
            authenticated_url = f"https://{username}:{pat}@{host}{path}"
            print(f"Constructed HTTPS URL from SSH with PAT: {authenticated_url[:authenticated_url.find('@') + 1]}... (hidden PAT)")
            return authenticated_url
        else:
            # Fallback for unhandled formats, or simply return as is if no PAT expected
            raise ValueError("Unsupported repository URL format for PAT authentication (expected HTTPS or SSH).")
    else:
        # Other schemes (e.g., git://, file://) are not supported with PAT injection here
        raise ValueError(f"Unsupported URL scheme for PAT authentication: {parsed_url.scheme}")


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/branches', methods=['POST'])
def get_branches():
    data = request.get_json()
    repo_url = data.get('repoUrl')
    pat = data.get('pat') # Get PAT from the frontend
    username = 'oauth2' # Common username for GitLab PATs, or could be 'private-token'

    print(f"Received request for branches: repoUrl={repo_url}, PAT provided={bool(pat)}")

    if not repo_url:
        return jsonify({"error": "Repository URL is required."}), 400

    git_command_url = repo_url
    if pat:
        try:
            git_command_url = construct_git_url_with_pat(repo_url, username, pat)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    print(f"Attempting to fetch branches from remote: {git_command_url} (PAT provided: {'Yes' if pat else 'No'})...")
    try:
        # Use 'ls-remote' directly on the remote URL, no local clone needed
        command = ['git', 'ls-remote', '--heads', git_command_url]
        print(f"Executing command: {' '.join(command)}")
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=FETCH_TIMEOUT_SECONDS # Apply timeout to ls-remote
        )
        lines = result.stdout.strip().split('\n')

        branches = []
        for line in lines:
            if '\trefs/heads/' in line:
                # Example: "hash\trefs/heads/branch_name"
                branch_name = line.split('\trefs/heads/')[1]
                if branch_name not in ['HEAD']: # Exclude HEAD pointer
                    branches.append(branch_name)

        # Prioritize main/master and sort
        branches = sorted(list(set(branches)))
        if 'main' in branches:
            branches.insert(0, branches.pop(branches.index('main')))
        elif 'master' in branches:
            branches.insert(0, branches.pop(branches.index('master')))

        print(f"Successfully fetched {len(branches)} branches.")
        return jsonify({"branches": branches})

    except subprocess.TimeoutExpired as e:
        print(f"Git ls-remote timed out: {e}")
        return jsonify({"error": f"Git operation timed out after {e.timeout} seconds. The repository might be slow to respond or network issues."}), 500
    except subprocess.CalledProcessError as e:
        error_output = e.stderr.strip()
        print(f"Error fetching branches (CalledProcessError): {error_output}")
        if "authentication" in error_output.lower() or "not authorized" in error_output.lower() or "bad credentials" in error_output.lower() or "access denied" in error_output.lower():
            return jsonify({"error": f"Authentication failed for {repo_url}. Please check your PAT and repository URL."}), 401
        elif "repository not found" in error_output.lower() or "not a git repository" in error_output.lower():
            return jsonify({"error": f"Repository not found or URL is incorrect: {repo_url}."}), 404
        else:
            return jsonify({"error": f"Failed to fetch branches: {error_output}"}), 500
    except Exception as e:
        print(f"An unexpected error occurred in get_branches: {e}")
        return jsonify({"error": f"An unexpected server error occurred: {str(e)}"}), 500


@app.route('/api/diff', methods=['POST'])
def get_diff():
    data = request.get_json()
    repo_url = data.get('repoUrl')
    pat = data.get('pat') # Get PAT from the frontend
    branch1 = data.get('branch1')
    branch2 = data.get('branch2')
    username = 'oauth2' # Common username for GitLab PATs

    print(f"Received request for diff: repoUrl={repo_url}, branch1={branch1}, branch2={branch2}, PAT provided={bool(pat)}")

    if not all([repo_url, branch1, branch2]):
        return jsonify({"error": "Repository URL, branch1, and branch2 are required."}), 400

    git_command_url = repo_url
    if pat:
        try:
            git_command_url = construct_git_url_with_pat(repo_url, username, pat)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    temp_dir = None
    try:
        # Create a temporary directory for the shallow clone
        temp_dir = tempfile.mkdtemp()
        print(f"Created temporary directory: {temp_dir}")

        # Perform a shallow clone of the repository into the temporary directory
        # --depth 1: Only fetch the latest commit on each branch.
        # --no-checkout: Don't checkout files immediately, just fetch the Git objects.
        print(f"Shallow cloning {git_command_url} into {temp_dir} (depth 1, no-checkout)...")
        subprocess.run(
            ['git', 'clone', '--depth', '1', '--no-checkout', git_command_url, temp_dir],
            check=True,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS
        )
        print(f"Successfully shallow cloned {repo_url} into {temp_dir}.")

        # Fetch the specific branches into the temporary clone.
        # This is crucial because --depth 1 might only get the default branch.
        # We need both branches to be present in the local object store for diffing.
        # We fetch into `refs/remotes/origin/` to make them accessible as `origin/branch_name`.
        print(f"Fetching branch {branch1} into temporary clone...")
        subprocess.run(
            ['git', '-C', temp_dir, 'fetch', 'origin', f'refs/heads/{branch1}:refs/remotes/origin/{branch1}'],
            check=True,
            capture_output=True,
            text=True,
            timeout=FETCH_TIMEOUT_SECONDS
        )
        print(f"Fetching branch {branch2} into temporary clone...")
        subprocess.run(
            ['git', '-C', temp_dir, 'fetch', 'origin', f'refs/heads/{branch2}:refs/remotes/origin/{branch2}'],
            check=True,
            capture_output=True,
            text=True,
            timeout=FETCH_TIMEOUT_SECONDS
        )
        print("Successfully fetched both branches into temporary clone.")

        # Now perform the diff within the temporary clone
        print(f"Getting diff for origin/{branch1}..origin/{branch2} in temp repo {temp_dir}")
        command = ['git', '-C', temp_dir, 'diff', f'origin/{branch1}..origin/{branch2}']
        print(f"Executing command: {' '.join(command)}")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=FETCH_TIMEOUT_SECONDS # Use fetch timeout for diff command
        )
        print("Diff command executed successfully.")
        return jsonify({"diff": result.stdout})

    except subprocess.TimeoutExpired as e:
        error_message = f"Git operation timed out after {e.timeout} seconds. The repository might be too large or your network too slow."
        print(f"Git command timed out: {e}")
        return jsonify({"error": error_message}), 500
    except subprocess.CalledProcessError as e:
        error_output = e.stderr.strip()
        print(f"Git command failed for diff (CalledProcessError): {error_output}")
        if "authentication" in error_output.lower() or "bad credentials" in error_output.lower() or "access denied" in error_output.lower():
            return jsonify({"error": f"Authentication failed when attempting diff. Please check your PAT and repository URL."}), 401
        elif "unknown revision" in error_output.lower() or "bad revision" in error_output.lower():
             return jsonify({"error": f"One or both branches ('{branch1}', '{branch2}') do not exist in the repository or are not accessible remotely. Please check branch names."}), 400
        elif "repository not found" in error_output.lower():
            return jsonify({"error": f"Repository not found or URL is incorrect: {repo_url}."}), 404
        else:
            return jsonify({"error": f"Failed to get diff: {error_output}"}), 500
    except Exception as e:
        print(f"An unexpected error occurred in get_diff: {e}")
        return jsonify({"error": f"An unexpected server error occurred: {str(e)}"}), 500
    finally:
        # Clean up the temporary directory
        if temp_dir and os.path.exists(temp_dir):
            print(f"Cleaning up temporary directory: {temp_dir}")
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            print("No temporary directory to clean up or it was already removed.")

if __name__ == '__main__':
    # When running directly, ensure the app is started.
    app.run(debug=True, host='0.0.0.0')