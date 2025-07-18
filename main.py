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


# def get_all_things_tasks():
#     return list(things.tasks())

def get_all_things_tasks():
    all_tasks = []
    for status in ["incomplete", "completed", "canceled"]:
        all_tasks.extend(list(things.tasks(status=status)))
    return all_tasks


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
    # First check if task has any actual dates assigned
    actual_date = None
    for field in ["start_date", "deadline"]:
        value = task.get(field)
        if value and value != "None":
            actual_date = value
            break
    
    # If no actual date is assigned, return None (keep Notion date blank)
    if not actual_date:
        return None
    
    # If task has a date AND is in Today list, use today's date (for overdue tasks)
    today_index = task.get('today_index')
    if today_index is not None:
        # Task is in Today list and has an actual date, use today's date
        today = datetime.now().strftime('%Y-%m-%d')
        return today
    
    # Otherwise use the actual scheduled date
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
        else:
            print(f"Skipped (no changes): {task['title']}")
    else:
        notion.pages.create(
            parent={"database_id": database_id}, properties=props)
        print(f"Created: {task['title']}")


# def delete_task_in_notion(notion, page_id):
#     notion.pages.update(page_id=page_id, archived=True)
#     print(f"Deleted (archived) Notion task {page_id}")


def sync_things_to_notion():
    notion_token = os.environ["NOTION_TOKEN"]
    notion_db_id = os.environ["NOTION_DATABASE_ID"]
    notion_projects_db_id = os.environ["NOTION_PROJECTS_DB_ID"]
    notion = Client(auth=notion_token)

    # Get complete sets from both sides up front
    all_tasks = get_all_things_tasks()
    tasks = get_things_todos(all_tasks)
    heading_lookup = build_heading_lookup(all_tasks)
    project_id_map = fetch_project_id_map(notion, notion_projects_db_id)
    notion_uuid_map = build_notion_uuid_map(notion, notion_db_id)

    # UUID sets for deletes
    things_uuid_set = set(task["uuid"] for task in tasks)

    # Add or update tasks
    for task in tasks:
        page = notion_uuid_map.get(task["uuid"])
        add_or_update_task_to_notion(
            notion, notion_db_id, task,
            heading_lookup, project_id_map, notion_projects_db_id,
            existing_page=page
        )

    # # Delete Notion tasks not found in Things anymore
    # for uuid, page in notion_uuid_map.items():
    #     if uuid not in things_uuid_set:
    #         delete_task_in_notion(notion, page["id"])


if __name__ == "__main__":
    sync_things_to_notion()
