import os
import json
import argparse
import subprocess
from dotenv import load_dotenv
from notion_client import Client
import things
from datetime import datetime, timedelta
from pathlib import Path

load_dotenv()


# Caching and optimization functions
CACHE_FILE = Path(__file__).parent / '.sync_cache.json'
LAST_SYNC_FILE = Path(__file__).parent / '.last_sync.json'
THINGS_DB_PATH = Path.home() / \
    "Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/Library/Application Support/ThingsData.db"


def load_cache():
    """Load cached task data with modification timestamps"""
    try:
        with open(CACHE_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"tasks": {}, "last_update": None}


def save_cache(cache_data):
    """Save task cache with timestamps"""
    cache_data["last_update"] = datetime.now().isoformat()
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache_data, f, indent=2)


def is_notion_active():
    """Check if Notion or Notion Calendar is the active app"""
    try:
        result = subprocess.run([
            'osascript', '-e',
            'tell application "System Events" to get name of first application process whose frontmost is true'
        ], capture_output=True, text=True, timeout=2)
        active_app = result.stdout.strip()
        return active_app in ["Notion", "Notion Calendar"]
    except:
        return False


def should_sync_based_on_focus(force=False):
    """Only sync when Notion apps are active or forced"""
    if force:
        return True

    if not is_notion_active():
        print("Notion not active, skipping sync...")
        return False

    # Check if we recently synced (avoid spam syncing)
    try:
        if LAST_SYNC_FILE.exists():
            with open(LAST_SYNC_FILE, 'r') as f:
                last_sync = datetime.fromisoformat(json.load(f)['last_sync'])
                if datetime.now() - last_sync < timedelta(seconds=30):
                    print("Recently synced, skipping...")
                    return False
    except:
        pass

    return True


def save_last_sync_time():
    """Save when we last synced"""
    with open(LAST_SYNC_FILE, 'w') as f:
        json.dump({'last_sync': datetime.now().isoformat()}, f)


def get_things_db_modified_time():
    """Get the last modified time of Things database"""
    try:
        if THINGS_DB_PATH.exists():
            return THINGS_DB_PATH.stat().st_mtime
    except:
        pass
    return None


def has_things_data_changed():
    """Check if Things database has changed since last sync"""
    try:
        current_mod_time = get_things_db_modified_time()
        if current_mod_time is None:
            return True  # Can't check, assume changed

        if LAST_SYNC_FILE.exists():
            with open(LAST_SYNC_FILE, 'r') as f:
                data = json.load(f)
                last_db_mod_time = data.get('things_db_mod_time')
                if last_db_mod_time and current_mod_time <= last_db_mod_time:
                    return False  # Database hasn't changed

        return True  # Database changed or first run
    except:
        return True  # Error, assume changed


def save_things_db_state():
    """Save current Things database state"""
    try:
        data = {'last_sync': datetime.now().isoformat()}
        mod_time = get_things_db_modified_time()
        if mod_time:
            data['things_db_mod_time'] = mod_time

        with open(LAST_SYNC_FILE, 'w') as f:
            json.dump(data, f)
    except:
        pass


def parse_things_date(date_str):
    """Parse Things date string to datetime object"""
    if not date_str or date_str == "None":
        return None
    try:
        # Things dates can be in various formats
        if 'T' in date_str:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        else:
            return datetime.strptime(date_str, '%Y-%m-%d')
    except:
        return None


def is_task_relevant(task):
    """Check if task should be synced - ONLY tasks with dates from 7 days ago onwards"""
    now = datetime.now()
    seven_days_ago = now - timedelta(days=7)

    # ONLY include tasks that have actual dates (start_date or deadline)
    # AND those dates are from 7 days ago to future
    for date_field in ['start_date', 'deadline']:
        task_date = parse_things_date(task.get(date_field))
        if task_date and task_date >= seven_days_ago:
            return True

    # Do NOT include tasks without dates, even if they're in Today
    return False


def get_filtered_things_tasks():
    """Get only relevant tasks based on optimization criteria"""
    print("Fetching Things tasks...")
    all_tasks = []
    for status in ["incomplete", "completed", "canceled"]:
        all_tasks.extend(list(things.tasks(status=status)))

    # Filter for relevance
    filtered_tasks = [task for task in all_tasks if is_task_relevant(task)]
    print(
        f"Filtered {len(filtered_tasks)} relevant tasks from {len(all_tasks)} total")

    return filtered_tasks


def get_all_things_tasks():
    """Backwards compatibility - use filtered version"""
    return get_filtered_things_tasks()


def get_things_todos(all_tasks):
    return [t for t in all_tasks if t.get("type") == "to-do"]


def build_heading_lookup(all_tasks):
    heading_lookup = {}
    for task in all_tasks:
        if task.get("type") == "heading":
            heading_lookup[task.get("uuid")] = {
                "project": task.get("project"),
                "project_title": task.get("project_title")
            }
    return heading_lookup


def get_task_project(task, heading_lookup):
    if task.get("project"):
        return task.get("project_title")
    elif task.get("heading") and task["heading"] in heading_lookup:
        return heading_lookup[task["heading"]]["project_title"]
    else:
        return None


def get_task_display_date(task):
    # Get the actual date from Things first
    actual_date = None
    for field in ["start_date", "deadline"]:
        value = task.get(field)
        if value and value != "None":
            actual_date = value
            break

    # If no date, return None
    if not actual_date:
        return None

    # NEVER change dates for completed tasks - they should keep their original dates
    task_status = task.get('status')
    if task_status == 'completed':
        # Completed tasks should always keep their original dates
        return actual_date
    
    # Only change dates for INCOMPLETE tasks that are overdue and in Today list
    today_index = task.get('today_index')
    if today_index is not None and task_status == 'incomplete':
        # Task is in Today list and incomplete - check if it's overdue
        task_date = parse_things_date(actual_date)
        today = datetime.now().date()

        if task_date and task_date.date() < today:
            # Task is overdue and in Today list, use today's date
            return datetime.now().strftime('%Y-%m-%d')

    # Otherwise, use the actual date from Things (could be today, future, or past)
    return actual_date


def fetch_project_id_map(notion, projects_db_id):
    project_map = {}
    results = []
    start_cursor = None
    while True:
        if start_cursor:
            page = notion.databases.query(
                database_id=projects_db_id, start_cursor=start_cursor)
        else:
            page = notion.databases.query(database_id=projects_db_id)
        results.extend(page["results"])
        if not page["has_more"]:
            break
        start_cursor = page.get("next_cursor")
    for item in results:
        titles = item["properties"]["Name"]["title"]
        if titles:
            name = titles[0]["plain_text"]
            project_map[name.strip()] = item["id"]
    return project_map


def create_project_in_notion(notion, projects_db_id, project_name):
    result = notion.pages.create(
        parent={"database_id": projects_db_id},
        properties={
            "Name": {"title": [{"text": {"content": project_name}}]}
        }
    )
    print(f"[INFO] Created project in Notion: {project_name}")
    return result["id"]


def get_or_create_project_id(project_name, project_id_map, notion, projects_db_id):
    if not project_name:
        return None
    for notion_name, pid in project_id_map.items():
        if project_name.strip().lower() == notion_name.strip().lower():
            return pid
    new_id = create_project_in_notion(notion, projects_db_id, project_name)
    project_id_map[project_name.strip()] = new_id
    return new_id


def fetch_all_notion_pages(notion, database_id):
    results = []
    start_cursor = None
    while True:
        if start_cursor:
            page = notion.databases.query(
                database_id=database_id, start_cursor=start_cursor)
        else:
            page = notion.databases.query(database_id=database_id)
        results.extend(page["results"])
        if not page.get("has_more"):
            break
        start_cursor = page.get("next_cursor")
    return results


def build_notion_uuid_map(notion, database_id):
    """
    Returns {Things UUID: Notion task page}
    """
    pages = fetch_all_notion_pages(notion, database_id)
    uuid_map = {}
    for page in pages:
        props = page["properties"]
        things_uuid = ""
        if (
            "Things UUID" in props and
            props["Things UUID"]["rich_text"] and
            "plain_text" in props["Things UUID"]["rich_text"][0]
        ):
            things_uuid = props["Things UUID"]["rich_text"][0]["plain_text"]
        if things_uuid:
            uuid_map[things_uuid] = page
    return uuid_map


def things_status_to_notion_status(things_status):
    # Map Things status to Notion status string
    # You can extend this function for more statuses as needed
    if things_status == "completed":
        return "Completed"
    elif things_status == "canceled":
        return "Canceled"
    elif things_status == "incomplete":
        return "Incomplete"
    else:
        return "Incomplete"  # fallback


def extract_date_part(date_string):
    """Extract just the date part from a date string, ignoring time"""
    if not date_string:
        return None

    # Handle ISO 8601 format from Notion (e.g. "2025-07-18T21:30:00.000+09:30")
    if 'T' in date_string:
        return date_string.split('T')[0]

    # Handle space-separated format (e.g. "2025-07-18 21:30:00")
    if ' ' in date_string:
        return date_string.split()[0]

    # Return as-is if it's just a date
    return date_string


def properties_differ(task, notion_page, project_id, date_value):
    props = notion_page["properties"]

    # Title
    notion_title = props["Name"]["title"][0]["plain_text"] if props["Name"]["title"] else ""
    if task["title"] != notion_title:
        return True

    # Status/select (safe access)
    status_prop = props.get("Status", {})
    status_value = status_prop.get("status")
    notion_status = status_value.get("name") if status_value else None
    things_status = things_status_to_notion_status(task.get("status"))
    if notion_status != things_status:
        return True

    # Project (relation)
    page_projects = props.get("Projects", {}).get("relation", [])
    notion_project_id = page_projects[0]["id"] if page_projects else None
    if (project_id or notion_project_id) and (project_id != notion_project_id):
        return True

    # Date - only trigger update if date parts are different
    notion_date = props.get("Date", {}).get("date", {})
    notion_date_value = notion_date.get("start") if notion_date else None

    things_date_part = extract_date_part(date_value)
    notion_date_part = extract_date_part(notion_date_value)

    # Only consider it different if date parts actually differ
    # Special case: if Things has no date but Notion has a date, we need to clear it
    if things_date_part is None and notion_date_part is not None:
        return True
    elif (things_date_part or notion_date_part) and (things_date_part != notion_date_part):
        return True

    return False


def task_properties_dict(task, heading_lookup, project_id_map, notion, projects_db_id, existing_page=None):
    props = {
        "Name": {"title": [{"text": {"content": task["title"]}}]},
        "Status": {"status": {"name": things_status_to_notion_status(task.get("status"))}},
        "Things UUID": {"rich_text": [{"text": {"content": task["uuid"]}}]}
    }
    project_name = get_task_project(task, heading_lookup)
    project_id = get_or_create_project_id(
        project_name, project_id_map, notion, projects_db_id)
    if project_id:
        props["Projects"] = {"relation": [{"id": project_id}]}

    date_value = get_task_display_date(task)
    if date_value:
        # If updating existing page, check if dates match
        if existing_page:
            existing_props = existing_page["properties"]
            existing_date = existing_props.get("Date", {}).get("date", {})
            existing_date_value = existing_date.get(
                "start") if existing_date else None

            # If dates match (same date part), don't update the date at all
            if (existing_date_value and
                    extract_date_part(date_value) == extract_date_part(existing_date_value)):
                # Don't add Date to props - keep existing date/time as is
                pass
            else:
                props["Date"] = {"date": {"start": date_value}}
        else:
            props["Date"] = {"date": {"start": date_value}}
    else:
        # Task has no date, clear the date in Notion
        props["Date"] = {"date": None}

    return props, project_id, date_value


def add_or_update_task_to_notion(notion, database_id, task, heading_lookup, project_id_map, projects_db_id, existing_page):
    props, project_id, date_value = task_properties_dict(
        task, heading_lookup, project_id_map, notion, projects_db_id, existing_page)

    if existing_page:
        if properties_differ(task, existing_page, project_id, date_value):
            notion.pages.update(page_id=existing_page["id"], properties=props)
            print(f"Updated: {task['title']}")
            return True  # Update made
        else:
            print(f"Skipped (no changes): {task['title']}")
            return False  # No update
    else:
        notion.pages.create(
            parent={"database_id": database_id}, properties=props)
        print(f"Created: {task['title']}")
        return True  # Creation made


# def delete_task_in_notion(notion, page_id):
#     notion.pages.update(page_id=page_id, archived=True)
#     print(f"Deleted (archived) Notion task {page_id}")


def sync_things_to_notion(force=False):
    """Optimized sync with caching and focus detection"""

    # Check if we should sync based on app focus
    if not should_sync_based_on_focus(force):
        return

    # Check if Things database has changed
    if not force and not has_things_data_changed():
        print("Things database unchanged, skipping sync...")
        return

    print("Starting optimized sync...")
    start_time = datetime.now()

    notion_token = os.environ["NOTION_TOKEN"]
    notion_db_id = os.environ["NOTION_DATABASE_ID"]
    notion_projects_db_id = os.environ["NOTION_PROJECTS_DB_ID"]
    notion = Client(auth=notion_token)

    # Load cache
    cache = load_cache()

    # Get filtered tasks (much smaller set)
    all_tasks = get_all_things_tasks()
    tasks = get_things_todos(all_tasks)

    print(f"Processing {len(tasks)} filtered tasks...")

    # Check cache to skip unchanged tasks
    tasks_to_sync = []
    for task in tasks:
        task_uuid = task["uuid"]
        task_mod_date = task.get("modification_date")

        # Skip if task hasn't changed since last sync
        if (task_uuid in cache["tasks"] and
                cache["tasks"][task_uuid].get("modification_date") == task_mod_date):
            continue

        tasks_to_sync.append(task)
        # Update cache
        cache["tasks"][task_uuid] = {
            "modification_date": task_mod_date,
            "last_synced": datetime.now().isoformat()
        }

    print(
        f"Syncing {len(tasks_to_sync)} changed tasks (skipped {len(tasks) - len(tasks_to_sync)} cached)")

    if not tasks_to_sync:
        print("No changes detected, sync completed")
        return

    # Only fetch these expensive resources if we have tasks to sync
    heading_lookup = build_heading_lookup(all_tasks)
    project_id_map = fetch_project_id_map(notion, notion_projects_db_id)
    notion_uuid_map = build_notion_uuid_map(notion, notion_db_id)

    # Sync only changed tasks
    updates_made = 0
    for task in tasks_to_sync:
        page = notion_uuid_map.get(task["uuid"])
        result = add_or_update_task_to_notion(
            notion, notion_db_id, task,
            heading_lookup, project_id_map, notion_projects_db_id,
            existing_page=page
        )
        if result:  # If update was made
            updates_made += 1

    # Save updated cache and sync time
    save_cache(cache)
    save_things_db_state()

    elapsed = datetime.now() - start_time
    print(
        f"Sync completed in {elapsed.total_seconds():.2f}s: {updates_made} updates made")


def sync_things_to_notion_legacy():
    """Original sync function for backwards compatibility"""
    notion_token = os.environ["NOTION_TOKEN"]
    notion_db_id = os.environ["NOTION_DATABASE_ID"]
    notion_projects_db_id = os.environ["NOTION_PROJECTS_DB_ID"]
    notion = Client(auth=notion_token)

    # Get complete sets from both sides up front
    all_tasks = []
    for status in ["incomplete", "completed", "canceled"]:
        all_tasks.extend(list(things.tasks(status=status)))

    tasks = get_things_todos(all_tasks)
    heading_lookup = build_heading_lookup(all_tasks)
    project_id_map = fetch_project_id_map(notion, notion_projects_db_id)
    notion_uuid_map = build_notion_uuid_map(notion, notion_db_id)

    # Add or update tasks
    for task in tasks:
        page = notion_uuid_map.get(task["uuid"])
        add_or_update_task_to_notion(
            notion, notion_db_id, task,
            heading_lookup, project_id_map, notion_projects_db_id,
            existing_page=page
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Sync Things tasks to Notion')
    parser.add_argument('--force', action='store_true',
                        help='Force sync even if Notion is not active')
    parser.add_argument('--legacy', action='store_true',
                        help='Use legacy sync (no optimizations)')
    parser.add_argument('--clear-cache', action='store_true',
                        help='Clear cache and force full sync')

    args = parser.parse_args()

    if args.clear_cache:
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()
            print("Cache cleared")

    if args.legacy:
        sync_things_to_notion_legacy()
    else:
        sync_things_to_notion(force=args.force)
