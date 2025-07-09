import os
from dotenv import load_dotenv
from notion_client import Client
import things

load_dotenv()


def get_things_tasks():
    # Get all tasks (all statuses)
    return things.tasks()


def build_heading_lookup(tasks):
    # Grab all headings and map them to their projects
    heading_lookup = {}
    for task in tasks:
        if task.get("type") == "heading":
            heading_lookup[task.get("uuid")] = {
                "project": task.get("project"),
                "project_title": task.get("project_title")
            }
    return heading_lookup


def get_task_project(task, heading_lookup):
    # Direct project
    if task.get("project"):
        return task.get("project_title")
    # Via heading
    elif task.get("heading") and task["heading"] in heading_lookup:
        return heading_lookup[task["heading"]]["project_title"]
    else:
        return None


def get_task_display_date(task):
    # Prefer start_date > deadline
    for field in ["start_date", "deadline"]:
        value = task.get(field)
        if value and value != "None":
            return value
    return None


def fetch_project_id_map(notion, projects_db_id):
    project_map = {}
    results = notion.databases.query(database_id=projects_db_id)["results"]
    for item in results:
        # Assuming the project name property in Notion is "Name"
        titles = item["properties"]["Name"]["title"]
        if titles:
            name = titles[0]["plain_text"]
            project_map[name.strip()] = item["id"]
    return project_map


def create_project_in_notion(notion, projects_db_id, project_name):
    # Create a new project in Notion and return its id
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
    # Loose match (case/space)
    for notion_name, pid in project_id_map.items():
        if project_name.strip().lower() == notion_name.strip().lower():
            return pid
    # Not found―create and update map
    new_id = create_project_in_notion(notion, projects_db_id, project_name)
    project_id_map[project_name.strip()] = new_id
    return new_id


def add_task_to_notion(notion, database_id, task, heading_lookup, project_id_map, projects_db_id):
    props = {
        "Name": {"title": [{"text": {"content": task["title"]}}]},
        "Status": {"checkbox": task.get("status") == "complete"}
    }
    project_name = get_task_project(task, heading_lookup)

    project_id = get_or_create_project_id(
        project_name,
        project_id_map,
        notion,
        projects_db_id
    )

    print(
        f"[DEBUG] Task: {task['title']} | Project from Things: {project_name} | Notion Project ID: {project_id}")

    if project_id:
        props["Projects"] = {"relation": [{"id": project_id}]}
    date_value = get_task_display_date(task)
    if date_value:
        props["Date"] = {"date": {"start": date_value}}
    notion.pages.create(
        parent={"database_id": database_id},
        properties=props
    )


def sync_things_to_notion():
    notion_token = os.environ["NOTION_TOKEN"]
    notion_db_id = os.environ["NOTION_DATABASE_ID"]
    notion_projects_db_id = os.environ["NOTION_PROJECTS_DB_ID"]
    notion = Client(auth=notion_token)

    tasks = get_things_tasks()
    heading_lookup = build_heading_lookup(tasks)
    project_id_map = fetch_project_id_map(notion, notion_projects_db_id)
    for task in tasks:
        if task["type"] != "to-do":
            continue
        add_task_to_notion(notion, notion_db_id, task,
                           heading_lookup, project_id_map, notion_projects_db_id)
        print(f"Synced: {task['title']}")


if __name__ == "__main__":
    sync_things_to_notion()
