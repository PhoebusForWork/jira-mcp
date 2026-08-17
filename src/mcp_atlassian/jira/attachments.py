"""Attachment operations for Jira API."""

import logging
import mimetypes
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from requests.exceptions import HTTPError

from ..models.jira import JiraAttachment
from ..utils.io import validate_safe_path
from ..utils.media import ATTACHMENT_MAX_BYTES
from .client import JiraClient
from .protocols import AttachmentsOperationsProto

# Configure logging
logger = logging.getLogger("mcp-jira")

# Wiki markup image references (e.g. "!photo_1.png|width=600!") that should
# be preserved verbatim when a Markdown body is converted to wiki markup.
# Restricted to filenames with image extensions so plain exclamation marks
# in prose are never touched.
WIKI_IMAGE_REF_PATTERN = re.compile(
    r"!([^!\r\n|]+\.(?:png|jpe?g|gif|webp|svg))(\|[^!\r\n]*)?!",
    re.IGNORECASE,
)


class AttachmentsMixin(JiraClient, AttachmentsOperationsProto):
    """Mixin for Jira attachment operations."""

    def download_attachment(self, url: str, target_path: str) -> bool:
        """
        Download a Jira attachment to the specified path.

        Args:
            url: The URL of the attachment to download
            target_path: The path where the attachment should be saved

        Returns:
            True if successful, False otherwise
        """
        if not url:
            logger.error("No URL provided for attachment download")
            return False

        try:
            # Convert to absolute path if relative
            if not os.path.isabs(target_path):
                target_path = os.path.abspath(target_path)

            # Guard against path traversal (resolves symlinks)
            validate_safe_path(target_path)

            logger.info(f"Downloading attachment from {url} to {target_path}")

            # Create the directory if it doesn't exist
            os.makedirs(os.path.dirname(target_path), exist_ok=True)

            # Use the Jira session to download the file
            response = self.jira._session.get(url, stream=True)
            response.raise_for_status()

            # Write the file to disk
            with open(target_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Verify the file was created
            if os.path.exists(target_path):
                file_size = os.path.getsize(target_path)
                logger.info(
                    f"Successfully downloaded attachment to {target_path} (size: {file_size} bytes)"
                )
                return True
            else:
                logger.error(f"File was not created at {target_path}")
                return False

        except Exception as e:
            logger.error(f"Error downloading attachment: {str(e)}")
            return False

    def fetch_attachment_content(self, url: str) -> bytes | None:
        """
        Fetch attachment content into memory.

        Args:
            url: The URL of the attachment to download

        Returns:
            The raw bytes of the attachment, or None on failure
        """
        if not url:
            logger.error("No URL provided for attachment fetch")
            return None

        try:
            logger.info(f"Fetching attachment from {url}")
            response = self.jira._session.get(url, stream=True)
            response.raise_for_status()

            chunks: list[bytes] = []
            for chunk in response.iter_content(chunk_size=8192):
                chunks.append(chunk)

            data = b"".join(chunks)
            logger.info(
                f"Successfully fetched attachment from {url} (size: {len(data)} bytes)"
            )
            return data

        except Exception as e:
            logger.error(f"Error fetching attachment: {str(e)}")
            return None

    def get_issue_attachments(self, issue_key: str) -> list[JiraAttachment]:
        """Return attachment metadata for a Jira issue without downloading.

        Args:
            issue_key: The Jira issue key (e.g., 'PROJ-123').

        Returns:
            A list of JiraAttachment instances.
        """
        logger.info(f"Fetching attachment metadata for {issue_key}")
        issue_data = self.jira.issue(issue_key, fields="attachment")

        if not isinstance(issue_data, dict):
            msg = f"Unexpected return value type from `jira.issue`: {type(issue_data)}"
            logger.error(msg)
            raise TypeError(msg)

        if "fields" not in issue_data:
            logger.error(f"Could not retrieve issue {issue_key}")
            return []

        attachment_data = issue_data.get("fields", {}).get("attachment", [])
        return [
            JiraAttachment.from_api_response(item)
            for item in attachment_data
            if isinstance(item, dict)
        ]

    def get_issue_attachment_contents(self, issue_key: str) -> dict[str, Any]:
        """
        Fetch all attachment contents for a Jira issue into memory.

        Unlike download_issue_attachments, this method does not write to
        the filesystem.  Each attachment is returned as raw bytes so the
        caller (e.g. the MCP server layer) can serialise them however it
        needs (base64 embedded resources, etc.).

        Args:
            issue_key: The Jira issue key (e.g., 'PROJ-123')

        Returns:
            A dictionary with:
                success (bool)
                issue_key (str)
                total (int)
                attachments (list[dict]): each dict has 'filename',
                    'content_type', 'size', and 'data' (bytes)
                failed (list[dict]): each dict has 'filename' and 'error'
        """
        logger.info(f"Fetching attachment contents for {issue_key}")

        issue_data = self.jira.issue(issue_key, fields="attachment")

        if not isinstance(issue_data, dict):
            msg = f"Unexpected return value type from `jira.issue`: {type(issue_data)}"
            logger.error(msg)
            raise TypeError(msg)

        if "fields" not in issue_data:
            logger.error(f"Could not retrieve issue {issue_key}")
            return {
                "success": False,
                "error": f"Could not retrieve issue {issue_key}",
            }

        attachment_data = issue_data.get("fields", {}).get("attachment", [])

        if not attachment_data:
            return {
                "success": True,
                "message": f"No attachments found for issue {issue_key}",
                "attachments": [],
                "failed": [],
            }

        attachments: list[JiraAttachment] = []
        for item in attachment_data:
            if isinstance(item, dict):
                attachments.append(JiraAttachment.from_api_response(item))

        if not attachments:
            return {
                "success": True,
                "message": f"No attachments found for issue {issue_key}",
                "attachments": [],
                "failed": [],
            }

        fetched: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for attachment in attachments:
            if not attachment.url:
                logger.warning(f"No URL for attachment {attachment.filename}")
                failed.append(
                    {"filename": attachment.filename, "error": "No URL available"}
                )
                continue

            if attachment.size > ATTACHMENT_MAX_BYTES:
                logger.warning(
                    f"Skipping attachment {attachment.filename}: "
                    f"{attachment.size} bytes exceeds 50 MB limit"
                )
                failed.append(
                    {
                        "filename": attachment.filename,
                        "error": (
                            f"Attachment '{attachment.filename}' is "
                            f"{attachment.size} bytes which exceeds "
                            "the 50 MB inline limit. Retrieve it "
                            "directly from Jira."
                        ),
                    }
                )
                continue

            data = self.fetch_attachment_content(attachment.url)
            if data is not None:
                content_type = (
                    attachment.content_type
                    or mimetypes.guess_type(attachment.filename)[0]
                    or "application/octet-stream"
                )
                fetched.append(
                    {
                        "filename": attachment.filename,
                        "content_type": content_type,
                        "size": len(data),
                        "data": data,
                    }
                )
            else:
                failed.append(
                    {"filename": attachment.filename, "error": "Fetch failed"}
                )

        return {
            "success": True,
            "issue_key": issue_key,
            "total": len(attachments),
            "attachments": fetched,
            "failed": failed,
        }

    def download_issue_attachments(
        self, issue_key: str, target_dir: str
    ) -> dict[str, Any]:
        """
        Download all attachments for a Jira issue.

        Args:
            issue_key: The Jira issue key (e.g., 'PROJ-123')
            target_dir: The directory where attachments should be saved

        Returns:
            A dictionary with download results
        """
        # Convert to absolute path if relative
        if not os.path.isabs(target_dir):
            target_dir = os.path.abspath(target_dir)

        # Guard against path traversal (resolves symlinks)
        validate_safe_path(target_dir)

        logger.info(
            f"Downloading attachments for {issue_key} to directory: {target_dir}"
        )

        # Create the target directory if it doesn't exist
        target_path = Path(target_dir)
        target_path.mkdir(parents=True, exist_ok=True)

        # Get the issue with attachments
        logger.info(f"Fetching issue {issue_key} with attachments")
        issue_data = self.jira.issue(issue_key, fields="attachment")

        if not isinstance(issue_data, dict):
            msg = f"Unexpected return value type from `jira.issue`: {type(issue_data)}"
            logger.error(msg)
            raise TypeError(msg)

        if "fields" not in issue_data:
            logger.error(f"Could not retrieve issue {issue_key}")
            return {"success": False, "error": f"Could not retrieve issue {issue_key}"}

        # Process attachments
        attachments = []
        results = []

        # Extract attachments from the API response
        attachment_data = issue_data.get("fields", {}).get("attachment", [])

        if not attachment_data:
            return {
                "success": True,
                "message": f"No attachments found for issue {issue_key}",
                "downloaded": [],
                "failed": [],
            }

        # Create JiraAttachment objects for each attachment
        for attachment in attachment_data:
            if isinstance(attachment, dict):
                attachments.append(JiraAttachment.from_api_response(attachment))

        # Download each attachment
        downloaded = []
        failed = []

        for attachment in attachments:
            if not attachment.url:
                logger.warning(f"No URL for attachment {attachment.filename}")
                failed.append(
                    {"filename": attachment.filename, "error": "No URL available"}
                )
                continue

            # Create a safe filename
            safe_filename = Path(attachment.filename).name
            file_path = target_path / safe_filename

            # Download the attachment
            success = self.download_attachment(attachment.url, str(file_path))

            if success:
                downloaded.append(
                    {
                        "filename": attachment.filename,
                        "path": str(file_path),
                        "size": attachment.size,
                    }
                )
            else:
                failed.append(
                    {"filename": attachment.filename, "error": "Download failed"}
                )

        return {
            "success": True,
            "issue_key": issue_key,
            "total": len(attachments),
            "downloaded": downloaded,
            "failed": failed,
        }

    def upload_attachment(self, issue_key: str, file_path: str) -> dict[str, Any]:
        """
        Upload a single attachment to a Jira issue.

        Args:
            issue_key: The Jira issue key (e.g., 'PROJ-123')
            file_path: The path to the file to upload

        Returns:
            A dictionary with upload result information
        """
        if not issue_key:
            logger.error("No issue key provided for attachment upload")
            return {"success": False, "error": "No issue key provided"}

        if not file_path:
            logger.error("No file path provided for attachment upload")
            return {"success": False, "error": "No file path provided"}

        try:
            # Convert to absolute path if relative
            if not os.path.isabs(file_path):
                file_path = os.path.abspath(file_path)

            # Check if file exists
            if not os.path.exists(file_path):
                logger.error(f"File not found: {file_path}")
                return {"success": False, "error": f"File not found: {file_path}"}

            filename = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            if file_size > ATTACHMENT_MAX_BYTES:
                logger.error(
                    f"Attachment {filename} is {file_size} bytes which exceeds "
                    "the 50 MB limit"
                )
                return {
                    "success": False,
                    "error": (
                        f"File '{filename}' is {file_size} bytes which exceeds "
                        "the 50 MB attachment limit."
                    ),
                }

            logger.info(f"Uploading attachment from {file_path} to issue {issue_key}")

            # Use the Jira API to upload the file
            attachment = self.jira.add_attachment(
                issue_key=issue_key, filename=file_path
            )

            if attachment:
                # The REST API returns a list of created attachments;
                # atlassian-python-api may also hand back a single dict.
                if isinstance(attachment, list) and attachment:
                    attachment = attachment[0]
                attachment_id = (
                    attachment.get("id") if isinstance(attachment, dict) else None
                )
                content_url = (
                    attachment.get("content") if isinstance(attachment, dict) else None
                )
                logger.info(
                    f"Successfully uploaded attachment {filename} to {issue_key} (size: {file_size} bytes)"
                )
                return {
                    "success": True,
                    "issue_key": issue_key,
                    "filename": filename,
                    "size": file_size,
                    "id": attachment_id,
                    "url": content_url,
                }
            else:
                logger.error(f"Failed to upload attachment {filename} to {issue_key}")
                return {
                    "success": False,
                    "error": f"Failed to upload attachment {filename} to {issue_key}",
                }

        except HTTPError as e:
            error_msg = self._attachment_http_error_message(e, issue_key)
            logger.error(f"Error uploading attachment: {error_msg}")
            return {"success": False, "error": error_msg}
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error uploading attachment: {error_msg}")
            return {"success": False, "error": error_msg}

    def upload_attachments(
        self, issue_key: str, file_paths: list[str]
    ) -> dict[str, Any]:
        """
        Upload multiple attachments to a Jira issue.

        Args:
            issue_key: The Jira issue key (e.g., 'PROJ-123')
            file_paths: List of paths to files to upload

        Returns:
            A dictionary with upload results
        """
        if not issue_key:
            logger.error("No issue key provided for attachment upload")
            return {"success": False, "error": "No issue key provided"}

        if not file_paths:
            logger.error("No file paths provided for attachment upload")
            return {"success": False, "error": "No file paths provided"}

        logger.info(f"Uploading {len(file_paths)} attachments to issue {issue_key}")

        # Upload each attachment
        uploaded = []
        failed = []

        for file_path in file_paths:
            result = self.upload_attachment(issue_key, file_path)

            if result.get("success"):
                uploaded.append(
                    {
                        "filename": result.get("filename"),
                        "size": result.get("size"),
                        "id": result.get("id"),
                    }
                )
            else:
                failed.append(
                    {
                        "filename": os.path.basename(file_path),
                        "error": result.get("error"),
                    }
                )

        return {
            "success": True,
            "issue_key": issue_key,
            "total": len(file_paths),
            "uploaded": uploaded,
            "failed": failed,
        }

    @staticmethod
    def _attachment_http_error_message(error: HTTPError, issue_key: str) -> str:
        """Translate an HTTPError from an attachment upload into a clear message."""
        status = error.response.status_code if error.response is not None else None
        if status == 403:
            return (
                f"Permission denied: you do not have permission to add "
                f"attachments to {issue_key} (HTTP 403)."
            )
        if status == 404:
            return f"Issue {issue_key} not found or not visible to you (HTTP 404)."
        if status == 413:
            return (
                "Attachment rejected by Jira: file exceeds the instance's "
                "attachment size limit (HTTP 413)."
            )
        return str(error)

    def delete_attachment(
        self,
        attachment_id: str | None = None,
        issue_key: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """
        Delete an attachment from a Jira issue.

        The attachment can be identified either directly by its ID, or by
        the combination of issue key and exact filename. When identifying by
        filename, the name must match exactly one attachment on the issue;
        otherwise an error listing the candidate IDs is returned.

        Args:
            attachment_id: The attachment ID to delete
            issue_key: The Jira issue key, used with filename
            filename: The exact attachment filename, used with issue_key

        Returns:
            A dictionary with the deletion result
        """
        if not attachment_id:
            if not (issue_key and filename):
                return {
                    "success": False,
                    "error": (
                        "Provide either attachment_id, or both issue_key and filename"
                    ),
                }
            try:
                matches = [
                    a
                    for a in self.get_issue_attachments(issue_key)
                    if a.filename == filename
                ]
            except Exception as e:
                logger.error(f"Error listing attachments for {issue_key}: {e}")
                return {
                    "success": False,
                    "error": f"Could not list attachments of {issue_key}: {e}",
                }
            if not matches:
                return {
                    "success": False,
                    "error": (f"No attachment named '{filename}' found on {issue_key}"),
                }
            if len(matches) > 1:
                ids = ", ".join(str(a.id) for a in matches)
                return {
                    "success": False,
                    "error": (
                        f"Multiple attachments named '{filename}' found on "
                        f"{issue_key} (ids: {ids}); use attachment_id to "
                        "select one"
                    ),
                }
            attachment_id = str(matches[0].id)

        try:
            self.jira.remove_attachment(attachment_id)
        except HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 403:
                error_msg = (
                    f"Permission denied: you do not have permission to "
                    f"delete attachment {attachment_id} (HTTP 403)."
                )
            elif status == 404:
                error_msg = f"Attachment {attachment_id} not found (HTTP 404)."
            else:
                error_msg = str(e)
            logger.error(f"Error deleting attachment: {error_msg}")
            return {"success": False, "error": error_msg}
        except Exception as e:
            logger.error(f"Error deleting attachment {attachment_id}: {e}")
            return {"success": False, "error": str(e)}

        result: dict[str, Any] = {
            "success": True,
            "attachment_id": attachment_id,
            "message": f"Attachment {attachment_id} deleted",
        }
        if filename:
            result["filename"] = filename
        if issue_key:
            result["issue_key"] = issue_key
        return result

    def upload_attachment_data(
        self, issue_key: str, filename: str, data: bytes
    ) -> dict[str, Any]:
        """
        Upload in-memory bytes as an attachment to a Jira issue.

        The bytes are written to a temporary file named ``filename`` so the
        attachment keeps the requested name in Jira.

        Args:
            issue_key: The Jira issue key (e.g., 'PROJ-123')
            filename: The filename the attachment should have in Jira
            data: The raw file content

        Returns:
            A dictionary with upload result information
        """
        if not issue_key:
            logger.error("No issue key provided for attachment upload")
            return {"success": False, "error": "No issue key provided"}

        if not filename:
            logger.error("No filename provided for attachment upload")
            return {"success": False, "error": "No filename provided"}

        if not data:
            logger.error("No data provided for attachment upload")
            return {"success": False, "error": "No file content provided"}

        if len(data) > ATTACHMENT_MAX_BYTES:
            return {
                "success": False,
                "error": (
                    f"File '{filename}' is {len(data)} bytes which exceeds "
                    "the 50 MB attachment limit."
                ),
            }

        # Strip any directory components to avoid path tricks in filenames
        safe_filename = Path(filename).name
        if not safe_filename:
            return {"success": False, "error": f"Invalid filename: {filename}"}

        with tempfile.TemporaryDirectory(prefix="mcp-jira-upload-") as tmp_dir:
            tmp_path = os.path.join(tmp_dir, safe_filename)
            with open(tmp_path, "wb") as f:
                f.write(data)
            return self.upload_attachment(issue_key, tmp_path)

    def _resolve_unique_attachment_filename(self, issue_key: str, filename: str) -> str:
        """Return a filename that does not collide with existing attachments.

        Wiki markup image references (``!filename!``) resolve by name, so a
        duplicate filename would make the reference ambiguous (and typically
        render the oldest attachment). When a collision is detected, a
        timestamp is inserted before the extension.

        Args:
            issue_key: The Jira issue key (e.g., 'PROJ-123')
            filename: The desired attachment filename

        Returns:
            The original filename, or a timestamped variant on collision
        """
        try:
            existing = {a.filename for a in self.get_issue_attachments(issue_key)}
        except Exception as e:
            logger.warning(f"Could not check existing attachments for {issue_key}: {e}")
            existing = set()

        if filename not in existing:
            return filename

        stem, ext = os.path.splitext(filename)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        candidate = f"{stem}_{timestamp}{ext}"
        counter = 1
        while candidate in existing:
            candidate = f"{stem}_{timestamp}-{counter}{ext}"
            counter += 1
        return candidate

    def _upload_image_for_embedding(
        self,
        issue_key: str,
        file_path: str | None = None,
        image_data: bytes | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """Upload an image so it can be referenced via wiki markup.

        Accepts either a local file path or raw bytes plus a filename, makes
        sure the attachment filename is unique on the issue, and uploads it.

        Returns:
            The upload result dict; on success it contains the final
            'filename' to reference in wiki markup.
        """
        if file_path is not None and image_data is not None:
            return {
                "success": False,
                "error": "Provide either file_path or image data, not both",
            }

        if file_path is not None:
            if not os.path.isabs(file_path):
                file_path = os.path.abspath(file_path)
            if not os.path.exists(file_path):
                return {"success": False, "error": f"File not found: {file_path}"}
            filename = os.path.basename(file_path)
            try:
                with open(file_path, "rb") as f:
                    image_data = f.read()
            except OSError as e:
                return {
                    "success": False,
                    "error": f"Could not read file {file_path}: {e}",
                }
        elif image_data is None:
            return {
                "success": False,
                "error": "Either file_path or image data is required",
            }

        if not filename:
            return {"success": False, "error": "No filename provided"}

        unique_filename = self._resolve_unique_attachment_filename(
            issue_key, Path(filename).name
        )
        return self.upload_attachment_data(issue_key, unique_filename, image_data)

    @staticmethod
    def _image_wiki_markup(filename: str, width: int | None = None) -> str:
        """Build the wiki markup reference for an attached image."""
        if width:
            return f"!{filename}|width={width}!"
        return f"!{filename}!"

    def embed_image_in_description(
        self,
        issue_key: str,
        file_path: str | None = None,
        image_data: bytes | None = None,
        filename: str | None = None,
        position: str = "append",
        width: int | None = None,
        marker: str | None = None,
    ) -> dict[str, Any]:
        """
        Upload an image and embed it in the issue description body.

        The image is attached to the issue, then referenced from the
        description using wiki markup (``!filename!``). The description is
        read and written through REST API v2, which uses wiki markup text on
        both Cloud and Server/DC, so the image renders inline in the
        description on the Jira web UI.

        With ``position='marker'``, the first occurrence of ``marker`` in
        the description is replaced by the image reference. This allows
        placing images anywhere — including inside table cells — by writing
        placeholder tokens (e.g. ``[[img:01]]``) into the description first.

        Args:
            issue_key: The Jira issue key (e.g., 'PROJ-123')
            file_path: Local path of the image file to upload
            image_data: Raw image bytes (alternative to file_path)
            filename: Filename for the attachment (required with image_data)
            position: Where to insert the image: 'append', 'prepend' or
                'marker'
            width: Optional rendered width in pixels
            marker: Placeholder text to replace (required with
                ``position='marker'``)

        Returns:
            A dictionary with the upload result, the wiki markup used and
            the update status
        """
        if position not in ("append", "prepend", "marker"):
            return {
                "success": False,
                "error": (
                    f"Invalid position '{position}': use 'append', 'prepend' "
                    "or 'marker'"
                ),
            }
        if position == "marker" and not marker:
            return {
                "success": False,
                "error": "position='marker' requires a non-empty 'marker' value",
            }

        try:
            # Read the current description via API v2 (wiki markup text)
            # before uploading, so a missing marker fails without leaving an
            # orphaned attachment behind.
            issue_data = self.jira.issue(issue_key, fields="description")
            description = ""
            if isinstance(issue_data, dict):
                description = issue_data.get("fields", {}).get("description") or ""
            if not isinstance(description, str):
                # Defensive: v2 should always return wiki markup text
                logger.warning(
                    f"Description of {issue_key} is not plain text; "
                    "appending image reference to an empty body"
                )
                description = ""
        except HTTPError as e:
            error_msg = self._attachment_http_error_message(e, issue_key)
            logger.error(f"Error reading description of {issue_key}: {error_msg}")
            return {"success": False, "error": error_msg}
        except Exception as e:
            logger.error(f"Error reading description of {issue_key}: {e}")
            return {"success": False, "error": str(e)}

        if position == "marker" and marker not in description:
            return {
                "success": False,
                "error": (
                    f"Marker '{marker}' not found in the description of "
                    f"{issue_key}; nothing was uploaded"
                ),
            }

        upload_result = self._upload_image_for_embedding(
            issue_key, file_path=file_path, image_data=image_data, filename=filename
        )
        if not upload_result.get("success"):
            return upload_result

        attached_filename = upload_result["filename"]
        markup = self._image_wiki_markup(attached_filename, width)

        try:
            if position == "marker":
                assert marker is not None  # noqa: S101 - validated above
                new_description = description.replace(marker, markup, 1)
            elif not description:
                new_description = markup
            elif position == "prepend":
                new_description = f"{markup}\n\n{description}"
            else:
                new_description = f"{description}\n\n{markup}"

            # Update via API v2 so wiki markup is preserved
            self.jira.update_issue(
                issue_key=issue_key,
                update={"fields": {"description": new_description}},
            )
        except HTTPError as e:
            error_msg = self._attachment_http_error_message(e, issue_key)
            logger.error(f"Error embedding image in description: {error_msg}")
            return {
                "success": False,
                "error": f"Image uploaded but description update failed: {error_msg}",
                "attachment": upload_result,
            }
        except Exception as e:
            logger.error(f"Error embedding image in description: {e}")
            return {
                "success": False,
                "error": f"Image uploaded but description update failed: {e}",
                "attachment": upload_result,
            }

        return {
            "success": True,
            "issue_key": issue_key,
            "attachment": upload_result,
            "image_markup": markup,
            "position": position,
            "message": (
                f"Image '{attached_filename}' uploaded and embedded in the "
                f"description of {issue_key}"
            ),
        }

    def _markdown_to_wiki_preserving_image_refs(self, body: str) -> str:
        """Convert a Markdown body to wiki markup, keeping image refs intact.

        Wiki image references (``!photo_1.png|width=600!``) inside the body
        would otherwise be mangled by the Markdown converter (underscores
        become emphasis, CJK filenames get escaped). Each reference is
        swapped for an inert token before conversion and restored verbatim
        afterwards, so any attachment filename can be referenced.
        """
        protected: dict[str, str] = {}

        def _stash(match: re.Match[str]) -> str:
            token = f"MCPWIKIIMGREF{len(protected)}Z"
            protected[token] = match.group(0)
            return token

        protected_body = WIKI_IMAGE_REF_PATTERN.sub(_stash, body)
        wiki_body = self.preprocessor.markdown_to_jira(protected_body)
        for token, ref in protected.items():
            wiki_body = wiki_body.replace(token, ref)
        return wiki_body

    def add_comment_with_image(
        self,
        issue_key: str,
        body: str,
        file_path: str | None = None,
        image_data: bytes | None = None,
        filename: str | None = None,
        width: int | None = None,
    ) -> dict[str, Any]:
        """
        Add a comment that displays one or more images inline.

        The comment is posted through REST API v2 with the body in wiki
        markup (Markdown input is converted), so wiki image references
        render inline on the Jira web UI. Jira Cloud converts them into
        internal ADF media nodes server-side.

        Two ways to get images into the comment, combinable:

        - Provide ``file_path`` or ``image_data``+``filename``: the file is
          uploaded and its reference appended to the comment.
        - Reference attachments that already exist on the issue directly in
          ``body`` using wiki syntax (``!photo_1.png|width=600!``). These
          references are preserved verbatim through the Markdown conversion,
          so filenames with underscores or non-ASCII characters are safe.

        Args:
            issue_key: The Jira issue key (e.g., 'PROJ-123')
            body: Comment text (Markdown), may be empty when uploading an
                image; may reference existing attachments with wiki syntax
            file_path: Local path of the image file to upload (optional)
            image_data: Raw image bytes (alternative to file_path)
            filename: Filename for the attachment (required with image_data)
            width: Optional rendered width in pixels for the uploaded image

        Returns:
            A dictionary with the upload result (if any), the wiki markup
            used and the created comment details
        """
        has_image = file_path is not None or image_data is not None
        if not has_image and not body:
            return {
                "success": False,
                "error": ("Provide a comment body, an image to upload, or both"),
            }

        upload_result: dict[str, Any] | None = None
        markup: str | None = None
        if has_image:
            upload_result = self._upload_image_for_embedding(
                issue_key,
                file_path=file_path,
                image_data=image_data,
                filename=filename,
            )
            if not upload_result.get("success"):
                return upload_result
            markup = self._image_wiki_markup(upload_result["filename"], width)

        try:
            wiki_body = ""
            if body:
                # Convert Markdown to wiki markup (not ADF): the comment is
                # posted via API v2 so image references stay intact
                wiki_body = self._markdown_to_wiki_preserving_image_refs(body)
            if markup and wiki_body:
                full_body = f"{wiki_body}\n\n{markup}"
            elif markup:
                full_body = markup
            else:
                full_body = wiki_body

            result = self.jira.issue_add_comment(issue_key, full_body)
            if not isinstance(result, dict):
                msg = (
                    "Unexpected return value type from "
                    f"`jira.issue_add_comment`: {type(result)}"
                )
                logger.error(msg)
                raise TypeError(msg)
        except HTTPError as e:
            error_msg = self._attachment_http_error_message(e, issue_key)
            logger.error(f"Error adding comment with image: {error_msg}")
            prefix = (
                "Image uploaded but adding the comment failed"
                if upload_result
                else "Adding the comment failed"
            )
            return {
                "success": False,
                "error": f"{prefix}: {error_msg}",
                "attachment": upload_result,
            }
        except Exception as e:
            logger.error(f"Error adding comment with image: {e}")
            prefix = (
                "Image uploaded but adding the comment failed"
                if upload_result
                else "Adding the comment failed"
            )
            return {
                "success": False,
                "error": f"{prefix}: {e}",
                "attachment": upload_result,
            }

        if upload_result:
            message = (
                f"Image '{upload_result['filename']}' uploaded and embedded "
                f"in a new comment on {issue_key}"
            )
        else:
            message = f"Comment added to {issue_key} with preserved image references"

        return {
            "success": True,
            "issue_key": issue_key,
            "attachment": upload_result,
            "image_markup": markup,
            "comment": {
                "id": result.get("id"),
                "created": result.get("created"),
                "author": result.get("author", {}).get("displayName", "Unknown"),
            },
            "message": message,
        }

    def edit_comment_with_image(
        self,
        issue_key: str,
        comment_id: str,
        body: str,
        file_path: str | None = None,
        image_data: bytes | None = None,
        filename: str | None = None,
        width: int | None = None,
    ) -> dict[str, Any]:
        """
        Replace a comment's body in place, with inline images preserved.

        The comment is updated through REST API v2 (``issue_edit_comment``)
        with the body in wiki markup, so wiki image references render inline
        after the edit — unlike the plain edit path, which converts to ADF
        and escapes image syntax. ``body`` REPLACES the entire comment, so
        it must be the complete new text.

        Images can come from two sources, combinable:

        - Reference attachments already on the issue in ``body`` with wiki
          syntax (``!photo_1.png|width=600!``); references are preserved
          verbatim through the Markdown conversion.
        - Provide ``file_path`` or ``image_data``+``filename`` to upload a
          new file and append its reference to the new body.

        Args:
            issue_key: The Jira issue key (e.g., 'PROJ-123')
            comment_id: The ID of the comment to replace
            body: The complete new comment text (Markdown); may reference
                existing attachments with wiki syntax
            file_path: Local path of an image file to upload (optional)
            image_data: Raw image bytes (alternative to file_path)
            filename: Filename for the attachment (required with image_data)
            width: Optional rendered width in pixels for the uploaded image

        Returns:
            A dictionary with the upload result (if any), the wiki markup
            used and the updated comment details
        """
        if not comment_id:
            return {"success": False, "error": "No comment_id provided"}

        has_image = file_path is not None or image_data is not None
        if not has_image and not body:
            return {
                "success": False,
                "error": "Provide a comment body, an image to upload, or both",
            }

        upload_result: dict[str, Any] | None = None
        markup: str | None = None
        if has_image:
            upload_result = self._upload_image_for_embedding(
                issue_key,
                file_path=file_path,
                image_data=image_data,
                filename=filename,
            )
            if not upload_result.get("success"):
                return upload_result
            markup = self._image_wiki_markup(upload_result["filename"], width)

        try:
            wiki_body = ""
            if body:
                # Convert Markdown to wiki markup (not ADF): the comment is
                # updated via API v2 so image references stay intact
                wiki_body = self._markdown_to_wiki_preserving_image_refs(body)
            if markup and wiki_body:
                full_body = f"{wiki_body}\n\n{markup}"
            elif markup:
                full_body = markup
            else:
                full_body = wiki_body

            result = self.jira.issue_edit_comment(issue_key, comment_id, full_body)
            if not isinstance(result, dict):
                msg = (
                    "Unexpected return value type from "
                    f"`jira.issue_edit_comment`: {type(result)}"
                )
                logger.error(msg)
                raise TypeError(msg)
        except HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 404:
                error_msg = f"Comment {comment_id} not found on {issue_key} (HTTP 404)."
            else:
                error_msg = self._attachment_http_error_message(e, issue_key)
            logger.error(f"Error editing comment with image: {error_msg}")
            prefix = (
                "Image uploaded but editing the comment failed"
                if upload_result
                else "Editing the comment failed"
            )
            return {
                "success": False,
                "error": f"{prefix}: {error_msg}",
                "attachment": upload_result,
            }
        except Exception as e:
            logger.error(f"Error editing comment with image: {e}")
            prefix = (
                "Image uploaded but editing the comment failed"
                if upload_result
                else "Editing the comment failed"
            )
            return {
                "success": False,
                "error": f"{prefix}: {e}",
                "attachment": upload_result,
            }

        return {
            "success": True,
            "issue_key": issue_key,
            "attachment": upload_result,
            "image_markup": markup,
            "comment": {
                "id": result.get("id"),
                "updated": result.get("updated"),
                "author": result.get("author", {}).get("displayName", "Unknown"),
            },
            "message": (
                f"Comment {comment_id} on {issue_key} replaced in place "
                "with inline image references preserved"
            ),
        }
