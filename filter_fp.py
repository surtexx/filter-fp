import os
import json
import requests
import asyncio
from copilot import CopilotClient
from copilot.session import PermissionHandler
import argparse
import re

def get_sonarqube_projects(sonarqube_url: str, token: str) -> list:
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(f"{sonarqube_url}/api/projects/search", headers=headers)
    response.raise_for_status()
    return [project["key"] for project in response.json().get("components", [])]

def get_sonarqube_issues(project_key: str, sonarqube_url: str, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "componentKeys": project_key,
        "statuses": "OPEN",
        "ps": 50 
    }
    
    response = requests.get(
        f"{sonarqube_url}/api/issues/search",
        headers=headers,
        params=params
    )
    response.raise_for_status()
    issues = {issue["rule"]: issue["key"] for issue in response.json().get("issues", [])}
    return issues

def filter_fp(answer: str, sonarqube_url: str, token: str, issues: dict) -> set:
    print("Applying false positive markings in SonarQube...")
    fp_issues = set(re.findall(r"\w+:S\d+", answer))
    for issue in fp_issues:
        issue_key = issues.get(issue)
        mark_response = requests.post(
            f"{sonarqube_url}/api/issues/do_transition",
            headers={"Authorization": f"Bearer {token}"},
            data={"issue": issue_key, "transition": "falsepositive"}
        )
        if mark_response.status_code == 200:
            print(f"Issue {issue_key} marked as false positive.")
        else:
            print(f"Failed to mark issue {issue_key}: {mark_response.text}")

def create_filter_prompt(issues: dict) -> str:
    simplified = [{"rule": rule, "key": key} for rule, key in issues.items()]
    issues_text = json.dumps(simplified, indent=2)
    
    return f"""Analyze the following SonarQube issues and help identify potential false positives.
For each issue, consider:
- Is this a real problem or a false positive?
- Is the severity appropriate?
- Any context that might make it a non-issue?

Issues:
{issues_text}

Please provide your analysis and recommendations for filtering. Only provide the issue keys (e.g., "rule:S1234") that you believe are false positives, along with a brief explanation for each."""

async def main():
    
    parser = argparse.ArgumentParser(description="Analyze SonarQube issues for false positives")
    parser.add_argument("--project_key", type=str, help="SonarQube project key")
    parser.add_argument("--apply", action="store_true", help="Mark false positives in SonarQube")
    parser.add_argument("--model", type=str, default="gpt-4.1", help="Model to use")
    parser.add_argument("--all_projects", action="store_true", help="Analyze all projects")
    parser.add_argument("--output", type=str, help="Output file for analysis results")
    parser.add_argument("--input", type=str, help="Input file for already parsed false positives")
    
    args = parser.parse_args()
    
    sonarqube_url = os.getenv("SONAR_HOST_URL")
    token = os.getenv("SONAR_TOKEN")
    
    project_keys = []
    if args.all_projects:
        project_keys = get_sonarqube_projects(sonarqube_url, token)
    elif args.project_key:
        project_keys = [args.project_key]
    else:
        print("Please specify a project key with --project_key or use --all_projects to analyze all projects.")
        return
    try:
        client = CopilotClient()
        await client.start()
        
        for project_key in project_keys:
            print(f"\nFetching issues from SonarQube for {project_key}...")
            issues = get_sonarqube_issues(project_key, sonarqube_url, token)
            
            if not issues:
                print("No open issues found.")
                continue
            
            print(f"Found {len(issues)} issues\n")
            
            if args.input:
                with open(args.input, "r") as f:
                    fp_issues = set(re.findall(r"\w+:S\d+", f.read()))
                    print(f"Loaded {len(fp_issues)} false positives from {args.input}")
                    if args.apply:
                        filter_fp(f.read(), sonarqube_url, token, issues)
            else:
                initial_prompt = create_filter_prompt(issues)
                session = await client.create_session(model=args.model, on_permission_request=PermissionHandler.approve_all, github_token=os.getenv("GH_TOKEN"))
                response = await session.send_and_wait(initial_prompt, timeout=300)
            
                if args.output:
                    with open(args.output, "a") as f:
                        f.write(response.data.content)
                        print(f"Analysis results saved to {args.output}")
                else: 
                    print(response.data.content)

                if args.apply:
                    filter_fp(response.data.content, sonarqube_url, token, issues)
        
        await client.stop()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())