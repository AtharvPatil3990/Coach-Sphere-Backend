import requests

API_URL = "http://127.0.0.1:8000/api/copilot/chat"

def query_copilot(prompt: str, role: str):
    print(f"\n=======================================================")
    print(f"ROLE: {role.upper()}")
    print(f"PROMPT: \"{prompt}\"")
    print(f"=======================================================")
    
    resp = requests.post(API_URL, json={"message": prompt, "user_role": role})
    
    if resp.status_code == 200:
        result = resp.json()
        print("\n[AI Summary]:", result["summary"])
        print("\n[Executed Auditable SQL]:\n", result["executed_sql"])
        print("\n[Returned Rows]:", result["data"])
        
        if result["action_preview"]:
            print("\n[STAGED ACTION CARD - Awaiting Approval]:")
            print(f"  • Type: {result['action_preview']['action_type']}")
            print(f"  • Recipients: {result['action_preview']['recipients_count']}")
            print(f"  • Draft Sample: \"{result['action_preview']['draft_sample']}\"")
            print(f"  • Status: {result['action_preview']['status']}")
    else:
        print(f"HTTP Error {resp.status_code}: {resp.json().get('detail')}")

if __name__ == "__main__":
    # Test 1: Academic Counselor asking for attendance + fee dues (Allowed)
    query_copilot(
        prompt="Which students in the Java batch have missed three or more classes and have an EMI due? Draft a WhatsApp reminder for each.",
        role="counselor"
    )

    # Test 2: Trainer asking for fee details (Must be blocked / masked)
    query_copilot(
        prompt="Show me all students who have not paid their fees yet.",
        role="trainer"
    )