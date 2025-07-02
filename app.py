from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import subprocess
import re
import sys
from urllib.parse import urlparse, urlunparse
import os
import tempfile
import shutil
from datetime import datetime # Import for date formatting

app = Flask(__name__)
CORS(app)

CLONE_TIMEOUT_SECONDS = 600
FETCH_TIMEOUT_SECONDS = 120
LOG_MAX_COMMITS = 500 # Increased limit for displaying full logs, adjust as needed

def construct_git_url_with_pat(repo_url, username, pat):
    """
    Constructs the Git URL with username and PAT for authentication.
    Handles both HTTPS and SSH URLs, converting SSH to HTTPS if a PAT is provided.
    """
    parsed_url = urlparse(repo_url)

    if pat:
        if parsed_url.scheme == 'https':
            clean_netloc = parsed_url.netloc
            if '@' in clean_netloc:
                clean_netloc = clean_netloc.split('@', 1)[-1]
            new_netloc = f"{username}:{pat}@{clean_netloc}"
            authenticated_url = urlunparse(parsed_url._replace(netloc=new_netloc))
            print(f"Constructed HTTPS URL with PAT: {authenticated_url[:authenticated_url.find('@') + 1]}... (hidden PAT)")
            return authenticated_url
        elif parsed_url.scheme == '' and parsed_url.netloc == '':
            if 'git@' in repo_url:
                ssh_parts = repo_url.split('git@', 1)
                if len(ssh_parts) < 2 or ':' not in ssh_parts[1]:
                    raise ValueError("Invalid SSH repository URL format.")
                host, path = ssh_parts[1].split(':', 1)
                if not path.startswith('/'):
                    path = '/' + path
                authenticated_url = f"https://{username}:{pat}@{host}{path}"
                print(f"Constructed HTTPS URL from SSH with PAT: {authenticated_url[:authenticated_url.find('@') + 1]}... (hidden PAT)")
                return authenticated_url
            else:
                raise ValueError("Unsupported repository URL format for PAT authentication (expected HTTPS or SSH).")
        else:
            raise ValueError(f"Unsupported URL scheme for PAT authentication: {parsed_url.scheme}")
    return repo_url

# Helper to run a git command and return stdout
def run_git_command(command_list, cwd=None, timeout=FETCH_TIMEOUT_SECONDS, error_message="Git command failed"):
    try:
        result = subprocess.run(
            command_list,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired as e:
        raise Exception(f"{error_message} (Timed out after {e.timeout}s): {e.cmd} - {e.stderr.strip()}")
    except subprocess.CalledProcessError as e:
        raise Exception(f"{error_message}: {e.cmd} - {e.stderr.strip()}")
    except Exception as e:
        raise Exception(f"An unexpected error running git command: {e}")

@app.route('/')
def index():
    return render_template('index.html')

def parse_git_log_output(log_output):
    commits_data = []
    if not log_output.strip():
        return commits_data

    # Use a more robust regex for splitting
    commit_blocks = re.split(r'---END-COMMIT---', log_output)

    for commit_block in commit_blocks:
        if not commit_block.strip():
            continue
        lines = commit_block.strip().split('\n')
        if len(lines) < 4:
            # print(f"Skipping malformed commit block: {commit_block[:50]}...")
            continue # Malformed block

        commit_hash = lines[0]
        commit_subject = lines[1]
        commit_author = lines[2]
        commit_date_str = lines[3] # Original date string from git
        # Parse and reformat date if needed for consistency, or send as is
        # For now, let's keep it as string to avoid timezone issues on backend
        # unless specifically required. Frontend can format.

        commits_data.append({
            "hash": commit_hash,
            "message": commit_subject,
            "author": commit_author,
            "date": commit_date_str # Keep as string
        })
    return commits_data

@app.route('/api/branches', methods=['POST'])
def get_branches():
    data = request.get_json()
    repo_url = data.get('repoUrl')
    pat = data.get('pat')
    username = 'oauth2'

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
        command = ['git', 'ls-remote', '--heads', git_command_url]
        print(f"Executing command: {' '.join(command)}")
        output = run_git_command(command, timeout=FETCH_TIMEOUT_SECONDS, error_message="Failed to list remote branches")
        lines = output.strip().split('\n')

        branches = []
        for line in lines:
            if '\trefs/heads/' in line:
                branch_name = line.split('\trefs/heads/')[1]
                if branch_name not in ['HEAD']: # Sometimes HEAD might appear
                    branches.append(branch_name)

        branches = sorted(list(set(branches)))
        if 'main' in branches:
            branches.insert(0, branches.pop(branches.index('main')))
        elif 'master' in branches:
            branches.insert(0, branches.pop(branches.index('master')))

        print(f"Successfully fetched {len(branches)} branches.")
        return jsonify({"branches": branches})

    except Exception as e:
        print(f"Error in get_branches: {e}")
        error_message = str(e)
        if "authentication" in error_message.lower() or "bad credentials" in error_message.lower() or "access denied" in error_message.lower():
            return jsonify({"error": f"Authentication failed for {repo_url}. Please check your PAT and repository URL."}), 401
        elif "repository not found" in error_message.lower() or "not a git repository" in error_message.lower():
            return jsonify({"error": f"Repository not found or URL is incorrect: {repo_url}."}), 404
        return jsonify({"error": error_message}), 500


@app.route('/api/compare_commits', methods=['POST'])
def compare_commits():
    data = request.get_json()
    repo_url = data.get('repoUrl')
    pat = data.get('pat')
    source_branch = data.get('sourceBranch') # Renamed from branch1
    destination_branch = data.get('destinationBranch') # Renamed from branch2
    username = 'oauth2'

    print(f"Received request for comparing commits: repoUrl={repo_url}, sourceBranch={source_branch}, destinationBranch={destination_branch}, PAT provided={bool(pat)}")

    if not all([repo_url, source_branch, destination_branch]):
        return jsonify({"error": "Repository URL, sourceBranch, and destinationBranch are required."}), 400

    git_command_url = repo_url
    if pat:
        try:
            git_command_url = construct_git_url_with_pat(repo_url, username, pat)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        print(f"Created temporary directory: {temp_dir}")

        # Shallow clone to get the repository structure and history
        # print(f"Cloning {git_command_url} into {temp_dir}...")
        run_git_command(
            ['git', 'clone', git_command_url, temp_dir],
            timeout=CLONE_TIMEOUT_SECONDS,
            error_message="Failed to clone repository"
        )
        # print(f"Successfully cloned {repo_url} into {temp_dir}.")

        # Fetch both branches to ensure they are available locally for log
        # print(f"Fetching source branch {source_branch} into temporary clone...")
        run_git_command(
            ['git', '-C', temp_dir, 'fetch', 'origin', f'refs/heads/{source_branch}:refs/remotes/origin/{source_branch}'],
            timeout=FETCH_TIMEOUT_SECONDS,
            error_message=f"Failed to fetch source branch {source_branch}"
        )
        # print(f"Fetching destination branch {destination_branch} into temporary clone...")
        run_git_command(
            ['git', '-C', temp_dir, 'fetch', 'origin', f'refs/heads/{destination_branch}:refs/remotes/origin/{destination_branch}'],
            timeout=FETCH_TIMEOUT_SECONDS,
            error_message=f"Failed to fetch destination branch {destination_branch}"
        )
        # print("Successfully fetched both branches into temporary clone.")

        # Get the full log for the source branch
        log_format = "--pretty=format:%H%n%s%n%an%n%ad%n---END-COMMIT---" # Hash, Subject, Author, Date
        # print(f"Getting commit log for source branch: origin/{source_branch}...")
        source_log_output = run_git_command(
            ['git', '-C', temp_dir, 'log', f'origin/{source_branch}', f'--max-count={LOG_MAX_COMMITS}', log_format],
            timeout=FETCH_TIMEOUT_SECONDS,
            error_message=f"Failed to get commit log for {source_branch}"
        )
        source_commits = parse_git_log_output(source_log_output)
        # print(f"Found {len(source_commits)} commits in source branch.")

        # Get the full log for the destination branch
        # print(f"Getting commit log for destination branch: origin/{destination_branch}...")
        destination_log_output = run_git_command(
            ['git', '-C', temp_dir, 'log', f'origin/{destination_branch}', f'--max-count={LOG_MAX_COMMITS}', log_format],
            timeout=FETCH_TIMEOUT_SECONDS,
            error_message=f"Failed to get commit log for {destination_branch}"
        )
        destination_commits = parse_git_log_output(destination_log_output)
        # print(f"Found {len(destination_commits)} commits in destination branch.")

        return jsonify({
            "source_commits": source_commits,
            "destination_commits": destination_commits
        })

    except Exception as e:
        print(f"Error in compare_commits: {e}")
        error_message = str(e)
        if "authentication" in error_message.lower() or "bad credentials" in error_message.lower() or "access denied" in error_message.lower():
            return jsonify({"error": f"Authentication failed when attempting commit comparison. Please check your PAT and repository URL."}), 401
        elif "unknown revision" in error_message.lower() or "bad revision" in error_message.lower():
            return jsonify({"error": f"One or both branches ('{source_branch}', '{destination_branch}') do not exist in the repository or are not accessible remotely. Please check branch names."}), 400
        elif "repository not found" in error_message.lower():
            return jsonify({"error": f"Repository not found or URL is incorrect: {repo_url}."}), 404
        return jsonify({"error": error_message}), 500
    finally:
        if temp_dir and os.path.exists(temp_dir):
            print(f"Cleaning up temporary directory: {temp_dir}")
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            print("No temporary directory to clean up or it was already removed.")

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')