from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import subprocess
import re
import sys
from urllib.parse import urlparse, urlunparse
import os
import tempfile
import shutil

app = Flask(__name__)
CORS(app)

CLONE_TIMEOUT_SECONDS = 600
FETCH_TIMEOUT_SECONDS = 120
LOG_MAX_COMMITS = 100000

def construct_git_url_with_pat(repo_url, username, pat):
   
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
                if branch_name not in ['HEAD']:
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


@app.route('/api/commits_and_diffs', methods=['POST'])
def get_commits_and_diffs():
    data = request.get_json()
    repo_url = data.get('repoUrl')
    pat = data.get('pat')
    branch1 = data.get('branch1')
    branch2 = data.get('branch2')
    username = 'oauth2'

    print(f"Received request for commits and diffs: repoUrl={repo_url}, branch1={branch1}, branch2={branch2}, PAT provided={bool(pat)}")

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
        temp_dir = tempfile.mkdtemp()
        print(f"Created temporary directory: {temp_dir}")

        
        print(f"Cloning {git_command_url} into {temp_dir}...")
        run_git_command(
            ['git', 'clone', git_command_url, temp_dir],
            timeout=CLONE_TIMEOUT_SECONDS,
            error_message="Failed to clone repository"
        )
        print(f"Successfully cloned {repo_url} into {temp_dir}.")

        
        print(f"Fetching branch {branch1} into temporary clone...")
        run_git_command(
            ['git', '-C', temp_dir, 'fetch', 'origin', f'refs/heads/{branch1}:refs/remotes/origin/{branch1}'],
            timeout=FETCH_TIMEOUT_SECONDS,
            error_message=f"Failed to fetch branch {branch1}"
        )
        print(f"Fetching branch {branch2} into temporary clone...")
        run_git_command(
            ['git', '-C', temp_dir, 'fetch', 'origin', f'refs/heads/{branch2}:refs/remotes/origin/{branch2}'],
            timeout=FETCH_TIMEOUT_SECONDS,
            error_message=f"Failed to fetch branch {branch2}"
        )
        print("Successfully fetched both branches into temporary clone.")

        
        print(f"Getting commit log for {branch1}..{branch2}...")
        log_format = "--pretty=format:%H%n%s%n%an%n%ad%n---END-COMMIT---" 
        log_output = run_git_command(
            ['git', '-C', temp_dir, 'log', f'origin/{branch1}..origin/{branch2}', f'--max-count={LOG_MAX_COMMITS}', log_format],
            timeout=FETCH_TIMEOUT_SECONDS,
            error_message="Failed to get commit log"
        )

        commits_raw = log_output.strip().split('---END-COMMIT---')
        commits_data = []

        if not log_output.strip():
            print("No commits found between the specified branches.")
            return jsonify({"commits": []})

        for commit_block in commits_raw:
            if not commit_block.strip():
                continue
            lines = commit_block.strip().split('\n')
            if len(lines) < 4:
                print(f"Skipping malformed commit block: {commit_block[:50]}...")
                continue 

            commit_hash = lines[0]
            commit_subject = lines[1]
            commit_author = lines[2]
            commit_date = lines[3]

            print(f"Fetching diff for commit {commit_hash}...")
        
            commit_diff = run_git_command(
                ['git', '-C', temp_dir, 'show', commit_hash],
                timeout=FETCH_TIMEOUT_SECONDS,
                error_message=f"Failed to get diff for commit {commit_hash}"
            )

 
            diff_lines = commit_diff.split('\n')
            diff_content = []
            in_diff_section = False
            for line in diff_lines:
                if line.startswith('diff --git'):
                    in_diff_section = True
                if in_diff_section:
                    diff_content.append(line)
            
            commits_data.append({
                "hash": commit_hash,
                "message": commit_subject,
                "author": commit_author,
                "date": commit_date,
                "diff": "\n".join(diff_content) 
            })
        
        print(f"Successfully processed {len(commits_data)} commits.")
        return jsonify({"commits": commits_data})

    except Exception as e:
        print(f"Error in get_commits_and_diffs: {e}")
        error_message = str(e)
        if "authentication" in error_message.lower() or "bad credentials" in error_message.lower() or "access denied" in error_message.lower():
            return jsonify({"error": f"Authentication failed when attempting diff. Please check your PAT and repository URL."}), 401
        elif "unknown revision" in error_message.lower() or "bad revision" in error_message.lower():
            return jsonify({"error": f"One or both branches ('{branch1}', '{branch2}') do not exist in the repository or are not accessible remotely. Please check branch names."}), 400
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