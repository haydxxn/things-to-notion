import os
from dotenv import load_dotenv
from notion_client import Client
import things

load_dotenv()


def get_all_things_tasks():
    # Fetches all tasks from Things (to-dos, headings, projects, etc.)
    return list(things.tasks())


def get_things_todos(all_tasks):
    # Filters only to-dos for syncing
    return [t for t in all_tasks if t.get("type") == "to-do"]


def build_heading_lookup(all_tasks):
    # Maps heading_uuid -> {project, project_title}
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
    for field in ["start_date", "deadline"]:
        value = task.get(field)
        if value and value != "None":
            return value
    return None


def fetch_project_id_map(notion, projects_db_id):
    project_map = {}
    results = notion.databases.query(database_id=projects_db_id)["results"]
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


def find_notion_page_for_task(notion, database_id, task_uuid):
    query = {
        "database_id": database_id,
        "filter": {
            "property": "Things UUID",
            "rich_text": {
                "equals": task_uuid
            }
        }
    }
    results = notion.databases.query(**query)["results"]
    return results[0] if results else None


def fetch_all_notion_pages(notion, database_id):
    results = []
    cursor = None
    while True:
        page = notion.databases.query(
            database_id=database_id, start_cursor=cursor) if cursor else notion.databases.query(database_id=database_id)
        results.extend(page["results"])
        cursor = page.get("next_cursor")
        if not page["has_more"]:
            break
    return results


def build_notion_uuid_map(notion, database_id):
    uuid_map = {}
    pages = fetch_all_notion_pages(notion, database_id)
    for page in pages:
        props = page["properties"]
        things_uuid = ""
        if ("Things UUID" in props and
            props["Things UUID"]["rich_text"] and
                "plain_text" in props["Things UUID"]["rich_text"][0]):
            things_uuid = props["Things UUID"]["rich_text"][0]["plain_text"]
        if things_uuid:
            uuid_map[things_uuid] = page["id"]
    return uuid_map


def properties_differ(task, notion_page, project_id, date_value):
    props = notion_page["properties"]

    # Title
    notion_title = props["Name"]["title"][0]["plain_text"] if props["Name"]["title"] else ""
    if task["title"] != notion_title:
        return True

    # Status/checkbox
    notion_status = props["Status"]["checkbox"]
    things_status = task.get("status") == "complete"
    if notion_status != things_status:
        return True

    # Project (relation)
    page_projects = props.get("Projects", {}).get("relation", [])
    notion_project_id = page_projects[0]["id"] if page_projects else None
    if (project_id or notion_project_id) and (project_id != notion_project_id):
        return True

    # Date
    notion_date = props.get("Date", {}).get("date", {})
    notion_date_value = notion_date.get("start") if notion_date else None
    if (date_value or notion_date_value) and (date_value != notion_date_value):
        return True

    return False


def add_or_update_task_to_notion(notion, database_id, task, heading_lookup, project_id_map, projects_db_id):
    props = {
        "Name": {"title": [{"text": {"content": task["title"]}}]},
        "Status": {"checkbox": task.get("status") == "complete"},
        "Things UUID": {"rich_text": [{"text": {"content": task["uuid"]}}]}
    }
    project_name = get_task_project(task, heading_lookup)
    project_id = get_or_create_project_id(
        project_name, project_id_map, notion, projects_db_id)
    if project_id:
        props["Projects"] = {"relation": [{"id": project_id}]}
    date_value = get_task_display_date(task)
    if date_value:
        props["Date"] = {"date": {"start": date_value}}
    page = find_notion_page_for_task(notion, database_id, task["uuid"])
    if page:
        if properties_differ(task, page, project_id, date_value):
            notion.pages.update(page_id=page["id"], properties=props)
            print(f"Updated: {task['title']}")
        else:
            print(f"Skipped (no changes): {task['title']}")
    else:
        notion.pages.create(
            parent={"database_id": database_id}, properties=props)
        print(f"Created: {task['title']}")


def delete_task_in_notion(notion, page_id):
    notion.pages.update(page_id=page_id, archived=True)
    print(f"Deleted (archived) Notion task {page_id}")


def sync_things_to_notion():
    notion_token = os.environ["NOTION_TOKEN"]
    notion_db_id = os.environ["NOTION_DATABASE_ID"]
    notion_projects_db_id = os.environ["NOTION_PROJECTS_DB_ID"]
    notion = Client(auth=notion_token)

    # Get full set of tasks for lookup, and to-dos for syncing
    all_tasks = get_all_things_tasks()
    tasks = get_things_todos(all_tasks)
    heading_lookup = build_heading_lookup(all_tasks)
    project_id_map = fetch_project_id_map(notion, notion_projects_db_id)

    things_uuid_set = set(task["uuid"] for task in tasks)
    notion_uuid_map = build_notion_uuid_map(notion, notion_db_id)

    # Add or update tasks
    for task in tasks:
        add_or_update_task_to_notion(notion, notion_db_id, task,
                                     heading_lookup, project_id_map, notion_projects_db_id)

    # Delete Notion tasks not found in Things anymore
    for uuid, page_id in notion_uuid_map.items():
        if uuid not in things_uuid_set:
            delete_task_in_notion(notion, page_id)


if __name__ == "__main__":
    sync_things_to_notion()
