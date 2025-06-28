import base64
import json
import os.path
from mimetypes import guess_type

from injector import inject

from taskweaver.llm import LLMApi, format_chat_message
from taskweaver.logging import TelemetryLogger
from taskweaver.memory import Memory, Post
from taskweaver.memory.attachment import AttachmentType
from taskweaver.module.event_emitter import SessionEventEmitter
from taskweaver.module.tracing import Tracing
from taskweaver.role import Role
from taskweaver.role.role import RoleConfig, RoleEntry
from taskweaver.session import SessionMetadata


# Function to encode a local image into data URL
def local_image_to_data_url(image_path):
    # Guess the MIME type of the image based on the file extension
    mime_type, _ = guess_type(image_path)
    if mime_type is None:
        mime_type = "application/octet-stream"  # Default MIME type if none is found

    try:
        # Read and encode the image file
        with open(image_path, "rb") as image_file:
            base64_encoded_data = base64.b64encode(image_file.read()).decode("utf-8")
    except FileNotFoundError:
        logger.error(f"Error: The file {image_path} does not exist.")
        return None
    except IOError:
        logger.error(f"Error: The file {image_path} could not be read.")
        return None
    # Construct the data URL
    return f"data:{mime_type};base64,{base64_encoded_data}"


class ImageReaderConfig(RoleConfig):
    def _configure(self):
        pass


class ImageReader(Role):
    @inject
    def __init__(
        self,
        config: ImageReaderConfig,
        logger: TelemetryLogger,
        tracing: Tracing,
        event_emitter: SessionEventEmitter,
        role_entry: RoleEntry,
        llm_api: LLMApi,
        session_metadata: SessionMetadata,
    ):
        super().__init__(config, logger, tracing, event_emitter, role_entry)

        self.llm_api = llm_api
        self.session_metadata = session_metadata

    def reply(self, memory: Memory, **kwargs: ...) -> Post:
        rounds = memory.get_role_rounds(
            role=self.alias,
            include_failure_rounds=False,
        )

        # obtain the query from the last round
        last_post = rounds[-1].post_list[-1]

        post_proxy = self.event_emitter.create_post_proxy(self.alias)

        post_proxy.update_send_to(last_post.send_from)

        input_message = last_post.message
        prompt = (
            f"Input message: {input_message}.\n"
            "\n"
            "Your response should be a JSON object with the key 'image_url' and the value as the image path. "
            "For example, {'image_url': 'c:/images/image.jpg'} or {'image_url': 'http://example.com/image.jpg'}. "
            "Do not add any additional information in the response or wrap the JSON with ```json and ```."
        )

        response = self.llm_api.chat_completion(
            messages=[
                format_chat_message(
                    role="system",
                    message="Your task is to read the image path from the message.",
                ),
                format_chat_message(
                    role="user",
                    message=prompt,
                ),
            ],
        )

        image_url = json.loads(response["content"])["image_url"]
        if image_url.startswith("http"):
            image_content = image_url
            attachment_message = f"Image from {image_url}."
        else:
            if os.path.isabs(image_url):
                image_content = local_image_to_data_url(image_url)
            else:
                image_content = local_image_to_data_url(os.path.join(self.session_metadata.execution_cwd, image_url))
            attachment_message = f"Image from {image_url} encoded as a Base64 data URL."

        post_proxy.update_attachment(
            message=attachment_message,
            type=AttachmentType.image_url,
            extra={"image_url": image_content},
            is_end=True,
        )

        post_proxy.update_message(
            "I have read the image path from the message. The image is attached below.",
        )

        return post_proxy.end()
