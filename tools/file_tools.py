from crewai.tools import BaseTool
from pathlib import Path
 
class ReadFileTool(BaseTool):
    name: str = "read_file"
    description: str = "Read the designated input file."
    input_path: str  # locked at instantiation

    def _run(self) -> str:
        try:
            return Path(self.input_path).read_text()
        except FileNotFoundError:
            return f"File not found: {self.input_path}"
 
class WriteFileTool(BaseTool):
    name: str = "write_file"
    description: str = "Write content to the designated output file."
    output_path: str  # locked at instantiation, not decided by LLM

    def _run(self, content: str) -> str:
        try:
            p = Path(self.output_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            return f"Written to {self.output_path}"
        except Exception as e:
            return f"Write error: {str(e)}"