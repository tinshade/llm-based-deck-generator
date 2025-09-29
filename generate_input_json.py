import os, json, traceback
from functools import wraps
from typing import Optional
from openai import OpenAI
from anthropic import Anthropic
from openai import APIError, APIConnectionError, RateLimitError, AuthenticationError #TODO: use these

 #TODO: Support other LLMs or move to a Generic LLM based approach

def with_key_failover(func):
    """Decorator to handle API key failover when AuthenticationError occurs."""
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        while self.current_key_index < len(self.api_keys):
            try:
                key = self.api_keys[self.current_key_index]
                self.client = self.model_details["caller"](api_key=key)
                return func(self, *args, **kwargs)
            except Exception as e:
                err_msg = str(e)
                if "401" in err_msg or "Incorrect API key" in err_msg:
                    print(f"Key {self.api_keys[self.current_key_index][:10]}... failed. Switching to next key.")
                    self.current_key_index += 1
                else:
                    raise
        raise RuntimeError("All API keys failed. Please check your keys.txt")
    return wrapper


class GeneratePPTJSON:
    def __init__(
        self, 
        model_to_use: str, 
        keys_file: str = "keys.txt", 
        output_file_path: str = "", 
        output_file_name: str = "presentation_output.json", 
        max_tokens: int = 4000
    ) -> None:
        self.model_to_use: str = model_to_use
        self.output_file_name: str = output_file_name
        self.output_file_path: str = output_file_path if output_file_path else os.path.join(os.getcwd(), output_file_name)
        self.max_tokens: int = max_tokens

        # Load keys from file
        keys_path = os.path.join(os.getcwd(), keys_file)
        if not os.path.exists(keys_path):
            raise FileNotFoundError(f"{keys_file} not found at {keys_path}")
        with open(keys_path, "r") as f:
            self.api_keys = [line.strip() for line in f if line.strip()]
        if not self.api_keys:
            raise ValueError(f"{keys_file} is empty!")

        self.current_key_index = 0

        # Config for providers
        self.llm_options: dict = {
            "OpenAI": {
                "caller": OpenAI,
                "model_version": "gpt-4o-mini",
                "is_active": True,
            },
            "Claude": {
                "caller": Anthropic,
                "model_version": "claude-3-5-sonnet-20241022",
                "is_active": True,
            },
        }

        if self.model_to_use not in self.llm_options:
            raise ValueError(f"Invalid model name: {self.model_to_use}. Valid: {list(self.llm_options.keys())}")

        self.model_details: dict = self.llm_options[self.model_to_use]
        self.client = self.model_details["caller"](api_key=self.api_keys[self.current_key_index])

        # Presentation templates
        #! Adjust according to your organization
        self.presentation_templates = {
            "Proposal": [
                "Problem Statement",
                "Solution Approach",
                "Success Criteria",
                "Key Benefits",
                "Risk Mitigation",
                "Commercials",
                "Terms and Conditions",
                "Next Steps"
            ],
            "Kickoff": [
                "Project Overview",
                "Team Introduction",
                "Project Scope",
                "Timeline and Milestones",
                "Roles and Responsibilities",
                "Communication Plan",
                "Success Criteria",
                "Next Steps"
            ],
            "Review": [
                "Executive Summary",
                "Progress Update",
                "Key Achievements",
                "Challenges and Resolutions",
                "Metrics and KPIs",
                "Next Phase Planning",
                "Action Items",
                "Q&A"
            ]
        }

    def read_file(self, file_path: str) -> str:
        """Read content from a file."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    def build_prompt(
        self, 
        requirements_doc: str, 
        questionnaire_answers: Optional[str] = None, 
        presentation_type: str = "Proposal"
    ) -> str:
        """Build the prompt for LLM based on inputs."""
        
        template_structure = self.presentation_templates.get(presentation_type, self.presentation_templates["Proposal"])
        
        prompt = f"""You are an AI assistant that generates business presentations.

Inputs:
1. Requirement document text
2. Questionnaire answers (if provided)
3. Presentation type: {presentation_type}

Task:
- Extract relevant information from the requirement document and questionnaire.
- Map the information into the defined structure for the given presentation type.
- If information is missing, list the clarifying questions at the end under "open_questions".
- Output in strict JSON format with this structure:

{{
  "presentation_type": "{presentation_type}",
  "project_title": "Extract from document or generate appropriate title",
  "slides": [
    {{"title": "Slide Title", "content": "Main content as string or structured sections"}},
    {{"title": "Slide Title", "sections": [
       {{"subtitle": "Section Name", "content": "Section content"}},
       {{"subtitle": "Another Section", "content": "More content"}}
    ]}}
  ],
  "open_questions": ["Question 1?", "Question 2?"]
}}

Expected slide structure for {presentation_type}: {', '.join(template_structure)}

Requirements Document:
{requirements_doc}

{'Questionnaire Answers:\n' + questionnaire_answers if questionnaire_answers else 'No questionnaire answers provided.'}

Constraints:
- Be concise and professional
- Fill as much as possible from the input
- Do not invent specifics unless logically implied
- Use bullet points for clarity where appropriate
- Ensure all content is actionable and relevant
- Generate valid JSON only, no additional text

Generate the presentation JSON now:"""

        return prompt

    @with_key_failover
    def _invoke_openai(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model_details["model_version"],
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": "You are a professional business presentation generator. Output valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    @with_key_failover
    def _invoke_claude(self, prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model_details["model_version"],
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        
        # Clean up markdown formatting if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
            text = text.replace("```json", "").replace("```", "").strip()
        
        return text

    def invoke_llm(self, prompt: str) -> dict:
        """Invoke the selected LLM with the prompt."""
        try:
            if self.model_to_use == "OpenAI":
                raw = self._invoke_openai(prompt)
            elif self.model_to_use == "Claude":
                raw = self._invoke_claude(prompt)
            else:
                raise ValueError("Unsupported model")

            return json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"JSON parsing error: {e}")
            print(f"Raw response: {raw[:500]}...")
            raise RuntimeError(f"Failed to parse JSON response from {self.model_to_use}")
        except Exception as e:
            traceback.print_exc()
            raise RuntimeError(f"Error invoking {self.model_to_use}: {e}")

    def save_json(self, jsonized_response: dict) -> str:
        """Save the JSON response to file."""
        with open(self.output_file_path, "w", encoding="utf-8") as f:
            json.dump(jsonized_response, f, indent=2, ensure_ascii=False)
        return f"JSON written to {self.output_file_path}"

    def generate_from_files(
        self, 
        requirements_file: str, 
        questionnaire_file: Optional[str] = None, 
        presentation_type: str = "Proposal"
    ) -> tuple[bool,str]:
        """
        Generate presentation JSON from file inputs.
        
        Args:
            requirements_file: Path to requirements document
            questionnaire_file: Path to questionnaire answers (optional)
            presentation_type: Type of presentation (Proposal, Kickoff, Review)
        
        Returns:
            Boolean status and string output file path | error
        """
        try:
            print(f"Reading requirements from: {requirements_file}")
            requirements_doc = self.read_file(requirements_file)
            
            questionnaire_answers = None
            if questionnaire_file:
                print(f"Reading questionnaire from: {questionnaire_file}")
                questionnaire_answers = self.read_file(questionnaire_file)
            
            print(f"Building prompt for {presentation_type} presentation...")
            prompt = self.build_prompt(requirements_doc, questionnaire_answers, presentation_type)
            
            print(f"Invoking {self.model_to_use} API...")
            data = self.invoke_llm(prompt)
            
            print("Saving JSON output...")
            return True, self.save_json(data)
        except Exception as e:
            return False, str(e)

    def generate_from_text(
        self, 
        requirements_text: str, 
        questionnaire_text: Optional[str] = None, 
        presentation_type: str = "Proposal"
    ) -> str:
        """
        Generate presentation JSON from text inputs.
        
        Args:
            requirements_text: Requirements document as string
            questionnaire_text: Questionnaire answers as string (optional)
            presentation_type: Type of presentation (Proposal, Kickoff, Review)
        
        Returns:
            Success message with output file path
        """
        print(f"Building prompt for {presentation_type} presentation...")
        prompt = self.build_prompt(requirements_text, questionnaire_text, presentation_type)
        
        print(f"Invoking {self.model_to_use} API...")
        data = self.invoke_llm(prompt)
        
        print("Saving JSON output...")
        return self.save_json(data)


# Example usage
if __name__ == "__main__":
    # Example 1: Generate from files
    try:
        generator = GeneratePPTJSON(
            model_to_use="Claude",  # or "OpenAI"
            output_file_name="proposal_output.json",
            max_tokens=4000
        )
        
        result = generator.generate_from_files(
            requirements_file="requirements.docx",  # Your requirements doc
            questionnaire_file=None,  # Optional questionnaire file
            presentation_type="Proposal"  # Proposal, Kickoff, or Review
        )
        
        print(result)

        '''
            # Example 2: Generate from text
            requirements_text = "Your requirements document text here..."
            result = generator.generate_from_text(
                requirements_text=requirements_text,
                presentation_type="Kickoff"
            )
        '''
        
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()
    