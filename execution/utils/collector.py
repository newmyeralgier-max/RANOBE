import os

def collect_project_context(root_path, output_file):
    """
    Собирает структуру проекта и содержимое ключевых файлов в один текст для ИИ.
    """
    exclude_dirs = {'.git', '.venv', '__pycache__', '.tmp', 'node_modules', '.agent'}
    include_extensions = {'.py', '.md', '.txt', '.toml', '.env.example'}

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("# Project Context Collector\n\n")
        
        f.write("## Directory Structure\n")
        for root, dirs, files in os.walk(root_path):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            level = root.replace(root_path, '').count(os.sep)
            indent = ' ' * 4 * level
            f.write(f"{indent}{os.path.basename(root)}/\n")
            sub_indent = ' ' * 4 * (level + 1)
            for file in files:
                f.write(f"{sub_indent}{file}\n")
        
        f.write("\n## File Contents\n")
        for root, dirs, files in os.walk(root_path):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for file in files:
                if any(file.endswith(ext) for ext in include_extensions):
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, root_path)
                    f.write(f"\n--- FILE: {rel_path} ---\n")
                    try:
                        with open(file_path, 'r', encoding='utf-8') as cf:
                            f.write(cf.read())
                    except Exception as e:
                        f.write(f"Error reading file: {e}\n")
                    f.write("\n--- END FILE ---\n")

if __name__ == "__main__":
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
    output = os.path.join(root, ".tmp", "project_context.txt")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    collect_project_context(root, output)
    print(f"Контекст собран в: {output}")
