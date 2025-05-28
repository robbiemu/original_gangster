import os
import subprocess
import json
from pathlib import Path
from smolagents.tools import tool


@tool
def count_files(path: str) -> int:
    """
    Return count of *path* itself and all nested files/directories.

    Args:
        path: A file or directory path to count contents under.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return -1
    if p.is_file():
        return 1
    try:
        return 1 + sum(1 for _ in p.rglob("*"))
    except Exception:
        return -1

@tool
def explore_directory_basic(path: str, max_depth: int = 2) -> str:
    """
    Basic directory exploration with standard permissions and ownership.
    
    Args:
        path: Directory path to explore (current directory if investigating query mentions it)
        max_depth: Maximum depth to traverse (default 2, max 5 for safety)
    
    Returns:
        JSON string with directory structure, permissions, and ownership
    """
    max_depth = min(max_depth, 5)  # Safety limit
    p = Path(path).expanduser().resolve()
    
    if not p.exists():
        return json.dumps({"error": f"Path does not exist: {p}"})
    
    if not p.is_dir():
        return json.dumps({"error": f"Path is not a directory: {p}"})
    
    try:
        result = subprocess.run(
            ["find", str(p), "-maxdepth", str(max_depth), "-ls"],
            capture_output=True, text=True, timeout=30
        )
        
        if result.returncode != 0:
            return json.dumps({"error": f"find command failed: {result.stderr}"})
        
        entries = []
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                parts = line.split(None, 10)
                if len(parts) >= 10:
                    entries.append({
                        "inode": parts[0],
                        "permissions": parts[2],
                        "links": parts[3],
                        "owner": parts[4],
                        "group": parts[5],
                        "size": parts[6],
                        "date": f"{parts[7]} {parts[8]} {parts[9]}",
                        "path": parts[10] if len(parts) > 10 else ""
                    })
        
        return json.dumps({
            "path": str(p),
            "entries_found": len(entries),
            "entries": entries[:100]  # Limit output size
        }, indent=2)
        
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "Directory exploration timed out"})
    except Exception as e:
        return json.dumps({"error": f"Failed to explore directory: {e}"})


@tool
def explore_directory_extended(path: str, max_depth: int = 1) -> str:
    """
    Extended directory exploration with macOS extended attributes and flags.
    
    Args:
        path: Directory path to explore
        max_depth: Maximum depth to traverse (default 1 for performance)
    
    Returns:
        JSON string with extended file system information
    """
    max_depth = min(max_depth, 3)  # Stricter limit for extended exploration
    p = Path(path).expanduser().resolve()
    
    if not p.exists():
        return json.dumps({"error": f"Path does not exist: {p}"})
    
    if not p.is_dir():
        return json.dumps({"error": f"Path is not a directory: {p}"})
    
    try:
        # Use ls -lO to get extended attributes and flags
        result = subprocess.run(
            ["find", str(p), "-maxdepth", str(max_depth), "-exec", "ls", "-lO", "{}", "+"],
            capture_output=True, text=True, timeout=45
        )
        
        if result.returncode != 0:
            return json.dumps({"error": f"Extended exploration failed: {result.stderr}"})
        
        entries = []
        current_entry = None
        
        for line in result.stdout.split('\n'):
            line = line.strip()
            if not line:
                continue
                
            # Parse ls -lO output
            if line.startswith(('d', '-', 'l', 'b', 'c', 'p', 's')):
                parts = line.split(None, 8)
                if len(parts) >= 8:
                    current_entry = {
                        "permissions": parts[0],
                        "links": parts[1],
                        "owner": parts[2],
                        "group": parts[3],
                        "flags": parts[4] if parts[4] != parts[5] else "",  # BSD flags
                        "size": parts[5] if parts[4] == parts[5] else parts[5],
                        "date": f"{parts[6] if parts[4] == parts[5] else parts[6]} {parts[7] if parts[4] == parts[5] else parts[7]}",
                        "path": parts[8] if parts[4] == parts[5] else parts[8],
                        "extended_attrs": []
                    }
                    entries.append(current_entry)
        
        return json.dumps({
            "path": str(p),
            "entries_found": len(entries),
            "entries": entries[:50]  # Limit for readability
        }, indent=2)
        
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "Extended exploration timed out"})
    except Exception as e:
        return json.dumps({"error": f"Extended exploration failed: {e}"})


@tool
def check_acls_and_xattrs(path: str) -> str:
    """
    Check Access Control Lists and Extended Attributes for a specific path.
    
    Args:
        path: File or directory path to examine
    
    Returns:
        JSON string with ACL and extended attribute information
    """
    p = Path(path).expanduser().resolve()
    
    if not p.exists():
        return json.dumps({"error": f"Path does not exist: {p}"})
    
    result = {
        "path": str(p),
        "acls": [],
        "extended_attributes": {},
        "quarantine_info": None,
        "code_signature": None
    }
    
    try:
        # Check for ACLs using ls -le
        acl_result = subprocess.run(
            ["ls", "-le", str(p)],
            capture_output=True, text=True, timeout=10
        )
        
        if acl_result.returncode == 0:
            lines = acl_result.stdout.strip().split('\n')
            for line in lines[1:]:  # Skip the first line (file info)
                line = line.strip()
                if line and line.startswith(' '):  # ACL entries are indented
                    result["acls"].append(line.strip())
        
        # Check extended attributes
        xattr_result = subprocess.run(
            ["xattr", "-l", str(p)],
            capture_output=True, text=True, timeout=10
        )
        
        if xattr_result.returncode == 0 and xattr_result.stdout.strip():
            current_attr = None
            for line in xattr_result.stdout.split('\n'):
                if line and not line.startswith('\t') and ':' in line:
                    # New attribute
                    attr_name = line.split(':')[0]
                    current_attr = attr_name
                    result["extended_attributes"][attr_name] = []
                elif line.startswith('\t') and current_attr:
                    # Continuation of attribute data
                    result["extended_attributes"][current_attr].append(line.strip())
        
        # Check for quarantine attribute specifically
        quarantine_result = subprocess.run(
            ["xattr", "-p", "com.apple.quarantine", str(p)],
            capture_output=True, text=True, timeout=5
        )
        
        if quarantine_result.returncode == 0:
            result["quarantine_info"] = quarantine_result.stdout.strip()
        
        # For executables, check code signature
        if p.is_file() and os.access(p, os.X_OK):
            codesign_result = subprocess.run(
                ["codesign", "-dv", str(p)],
                capture_output=True, text=True, timeout=10
            )
            
            if codesign_result.returncode == 0 or codesign_result.stderr:
                result["code_signature"] = codesign_result.stderr.strip()
    
    except subprocess.TimeoutExpired:
        result["error"] = "ACL/Extended attribute check timed out"
    except Exception as e:
        result["error"] = f"Failed to check ACLs/extended attributes: {e}"
    
    return json.dumps(result, indent=2)


@tool
def analyze_path_security(path: str) -> str:
    """
    Comprehensive security analysis of a path including SIP, permissions, and ownership chains.
    
    Args:
        path: Path to analyze for security implications
    
    Returns:
        JSON string with security analysis
    """
    p = Path(path).expanduser().resolve()
    
    if not p.exists():
        return json.dumps({"error": f"Path does not exist: {p}"})
    
    analysis = {
        "path": str(p),
        "absolute_path": str(p.resolve()),
        "is_system_path": False,
        "is_sip_protected": False,
        "ownership_chain": [],
        "permission_analysis": {},
        "security_concerns": []
    }
    
    try:
        # Check if it's a system path
        system_paths = ["/System", "/usr", "/bin", "/sbin", "/etc", "/var", "/Library"]
        if any(str(p).startswith(sys_path) for sys_path in system_paths):
            analysis["is_system_path"] = True
        
        # Check SIP protection
        if analysis["is_system_path"]:
            sip_result = subprocess.run(
                ["csrutil", "status"],
                capture_output=True, text=True, timeout=5
            )
            if "enabled" in sip_result.stdout.lower():
                analysis["is_sip_protected"] = True
        
        # Analyze ownership chain up to root
        current_path = p
        while current_path != current_path.parent:
            try:
                stat_result = subprocess.run(
                    ["stat", "-f", "%u:%g:%p:%N", str(current_path)],
                    capture_output=True, text=True, timeout=5
                )
                
                if stat_result.returncode == 0:
                    uid, gid, perms, name = stat_result.stdout.strip().split(':', 3)
                    analysis["ownership_chain"].append({
                        "path": name,
                        "uid": uid,
                        "gid": gid,
                        "permissions": perms
                    })
                
                current_path = current_path.parent
                if len(analysis["ownership_chain"]) > 10:  # Prevent infinite loops
                    break
                    
            except Exception:
                break
        
        # Permission analysis
        stat_result = subprocess.run(
            ["stat", "-f", "%Sp %u %g %z", str(p)],
            capture_output=True, text=True, timeout=5
        )
        
        if stat_result.returncode == 0:
            perms, uid, gid, size = stat_result.stdout.strip().split()
            analysis["permission_analysis"] = {
                "permissions": perms,
                "uid": uid,
                "gid": gid,
                "size": size,
                "world_writable": "w" in perms[-3:],
                "group_writable": "w" in perms[4:7],
                "owner_writable": "w" in perms[1:4]
            }
        
        # Security concerns
        if analysis["permission_analysis"].get("world_writable"):
            analysis["security_concerns"].append("World-writable permissions")
        
        if analysis["is_system_path"] and not analysis["is_sip_protected"]:
            analysis["security_concerns"].append("System path without SIP protection")
        
        # Check for unusual ownership
        current_uid = os.getuid()
        if analysis["permission_analysis"].get("uid") and int(analysis["permission_analysis"]["uid"]) != current_uid and int(analysis["permission_analysis"]["uid"]) != 0:
            analysis["security_concerns"].append("File owned by different user")
    
    except subprocess.TimeoutExpired:
        analysis["error"] = "Security analysis timed out"
    except Exception as e:
        analysis["error"] = f"Security analysis failed: {e}"
    
    return json.dumps(analysis, indent=2)


@tool
def explore_specific_path(path: str, context_note: str = "") -> str:
    """
    Explore a specific file or directory path that was mentioned in the user query.
    
    IMPORTANT: Only use this tool to investigate *filesystem paths* that are explicitly mentioned 
    or clearly implied in the user's request. Do not use for general exploration.
    
    Args:
        path: The specific path mentioned in the user query (e.g., "config.txt", "../data", "~/Documents")
        context_note: Brief note about why you're exploring this path (optional)
    
    Returns:
        JSON string with detailed analysis of the specified path
    """
    try:
        p = Path(path).expanduser().resolve()
        current_dir = Path(".").resolve()
        
        analysis = {
            "requested_path": path,
            "resolved_path": str(p),
            "relative_to_cwd": str(p.relative_to(current_dir)) if current_dir in p.parents or p == current_dir else "outside_cwd",
            "context_note": context_note,
            "exists": p.exists(),
            "accessible": False,
            "details": {}
        }
        
        if not p.exists():
            analysis["details"]["status"] = "does_not_exist"
            return json.dumps(analysis, indent=2)
        
        # Test accessibility
        try:
            p.stat()
            analysis["accessible"] = True
        except PermissionError:
            analysis["details"]["status"] = "permission_denied"
            return json.dumps(analysis, indent=2)
        except Exception as e:
            analysis["details"]["error"] = str(e)
            return json.dumps(analysis, indent=2)
        
        # Get detailed information
        analysis["details"].update({
            "is_file": p.is_file(),
            "is_directory": p.is_dir(),
            "is_symlink": p.is_symlink()
        })
        
        # Basic stat info
        stat_result = subprocess.run(
            ["stat", "-f", "%Sp %u:%g %z %N", str(p)],
            capture_output=True, text=True, timeout=5
        )
        
        if stat_result.returncode == 0:
            parts = stat_result.stdout.strip().rsplit(' ', 1)
            if len(parts) == 2:
                stat_info, name = parts
                perms, owner_group, size = stat_info.split(' ', 2)
                analysis["details"].update({
                    "permissions": perms,
                    "owner_group": owner_group,
                    "size": size,
                    "name": name
                })
        
        # If directory, get basic contents info
        if p.is_dir():
            try:
                children = list(p.iterdir())
                analysis["details"]["child_count"] = len(children)
                analysis["details"]["sample_children"] = [child.name for child in children[:10]]
                
                # Check for significant subdirectories
                subdirs = [child for child in children if child.is_dir()]
                if subdirs:
                    analysis["details"]["subdirectory_count"] = len(subdirs)
                    analysis["details"]["sample_subdirs"] = [d.name for d in subdirs[:5]]
                    
            except PermissionError:
                analysis["details"]["contents"] = "permission_denied"
            except Exception as e:
                analysis["details"]["contents_error"] = str(e)
        
        # Check for extended attributes if accessible
        if analysis["accessible"]:
            xattr_result = subprocess.run(
                ["xattr", "-l", str(p)],
                capture_output=True, text=True, timeout=5
            )
            
            if xattr_result.returncode == 0 and xattr_result.stdout.strip():
                analysis["details"]["has_extended_attributes"] = True
                analysis["details"]["xattr_summary"] = xattr_result.stdout.strip().split('\n')[:3]  # First few lines
            else:
                analysis["details"]["has_extended_attributes"] = False
    
    except Exception as e:
        analysis = {
            "requested_path": path,
            "context_note": context_note,
            "error": f"Failed to analyze path: {e}"
        }
    
    return json.dumps(analysis, indent=2)

def get_auditor_tools():
    """
    Returns list of directory exploration tools for the auditor.
    """

    return [
        explore_directory_basic,
        explore_directory_extended, 
        check_acls_and_xattrs,
        analyze_path_security,
        explore_specific_path,
        count_files  
    ]
