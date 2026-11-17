import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:googleapis/gmail/v1.dart' as gmail;
import 'package:googleapis/drive/v3.dart' as drive;
import 'package:http/http.dart' as http;
import 'package:google_sign_in/google_sign_in.dart';
import 'package:extension_google_sign_in_as_googleapis_auth/extension_google_sign_in_as_googleapis_auth.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/core/security/aura_secure_storage.dart';
import 'package:aura_client/l10n/l10n.dart';

// ----------------------------------------------------
// Models
// ----------------------------------------------------

class EmailActionItem {
  final String title;
  final DateTime? dueDate;

  EmailActionItem({required this.title, this.dueDate});

  factory EmailActionItem.fromMap(Map<String, dynamic> map) {
    DateTime? due;
    if (map['due_date'] != null) {
      due = DateTime.tryParse(map['due_date']);
    }
    return EmailActionItem(
      title: map['title'] ?? '',
      dueDate: due,
    );
  }
}

class EmailAnalysisResult {
  final String category;
  final String summary;
  final String urgency;
  final List<EmailActionItem> actionItems;
  final bool shouldCreateEvent;
  final String? eventTitle;
  final DateTime? eventStart;
  final DateTime? eventEnd;

  EmailAnalysisResult({
    required this.category,
    required this.summary,
    required this.urgency,
    required this.actionItems,
    required this.shouldCreateEvent,
    this.eventTitle,
    this.eventStart,
    this.eventEnd,
  });

  factory EmailAnalysisResult.fromMap(Map<String, dynamic> map) {
    final list = (map['action_items'] as List?) ?? [];
    final items = list.map((item) => EmailActionItem.fromMap(item as Map<String, dynamic>)).toList();

    return EmailAnalysisResult(
      category: map['category'] ?? 'General',
      summary: map['summary'] ?? '',
      urgency: map['urgency'] ?? 'low',
      actionItems: items,
      shouldCreateEvent: map['should_create_event'] ?? false,
      eventTitle: map['event_title'],
      eventStart: map['event_start'] != null ? DateTime.tryParse(map['event_start']) : null,
      eventEnd: map['event_end'] != null ? DateTime.tryParse(map['event_end']) : null,
    );
  }
}

class GitHubIssueResult {
  final String summary;
  final List<String> subtasks;
  final String complexity;
  final String suggestedPriority;

  GitHubIssueResult({
    required this.summary,
    required this.subtasks,
    required this.complexity,
    required this.suggestedPriority,
  });

  factory GitHubIssueResult.fromMap(Map<String, dynamic> map) {
    final list = (map['subtasks'] as List?) ?? [];
    return GitHubIssueResult(
      summary: map['summary'] ?? '',
      subtasks: list.map((e) => e.toString()).toList(),
      complexity: map['complexity'] ?? 'medium',
      suggestedPriority: map['suggested_priority'] ?? 'medium',
    );
  }
}

class GmailMessageHeader {
  final String id;
  final String subject;
  final String sender;
  final String snippet;
  final String body;

  GmailMessageHeader({
    required this.id,
    required this.subject,
    required this.sender,
    required this.snippet,
    required this.body,
  });
}

class GitHubIssueItem {
  final int number;
  final String title;
  final String htmlUrl;
  final String body;

  GitHubIssueItem({
    required this.number,
    required this.title,
    required this.htmlUrl,
    required this.body,
  });

  factory GitHubIssueItem.fromMap(Map<String, dynamic> map) {
    return GitHubIssueItem(
      number: map['number'] ?? 0,
      title: map['title'] ?? '',
      htmlUrl: map['html_url'] ?? '',
      body: map['body'] ?? '',
    );
  }
}

// ----------------------------------------------------
// Repository
// ----------------------------------------------------

class AutomationRepository {
  final _storage = auraSecureStorage;
  final Ref _ref;

  AutomationRepository(this._ref);

  // Secure token storage for GitHub PAT
  Future<String?> getGitHubToken() async {
    return await _storage.read(key: 'github_pat');
  }

  Future<void> saveGitHubToken(String token) async {
    await _storage.write(key: 'github_pat', value: token);
  }

  // Gmail Integrations: Fetch unread messages
  Future<List<GmailMessageHeader>> fetchUnreadEmails(http.Client authClient) async {
    final gmailApi = gmail.GmailApi(authClient);
    final listResponse = await gmailApi.users.messages.list('me', q: 'is:unread', maxResults: 10);
    
    final results = <GmailMessageHeader>[];
    final messages = listResponse.messages ?? [];

    for (final msg in messages) {
      if (msg.id == null) continue;
      final fullMsg = await gmailApi.users.messages.get('me', msg.id!);
      
      String subject = currentL10n.automationsGmailNoSubject;
      String sender = currentL10n.automationsGmailUnknownSender;
      
      final headers = fullMsg.payload?.headers ?? [];
      for (final header in headers) {
        if (header.name == 'Subject') subject = header.value ?? subject;
        if (header.name == 'From') sender = header.value ?? sender;
      }

      final body = _extractBody(fullMsg);
      results.add(GmailMessageHeader(
        id: msg.id!,
        subject: subject,
        sender: sender,
        snippet: fullMsg.snippet ?? "",
        body: body.isNotEmpty ? body : (fullMsg.snippet ?? ""),
      ));
    }

    return results;
  }

  String _extractBody(gmail.Message message) {
    final payload = message.payload;
    if (payload == null) return "";
    
    if (payload.body?.data != null) {
      return _decodeBase64(payload.body!.data!);
    }
    
    final parts = payload.parts;
    if (parts != null) {
      for (final part in parts) {
        if (part.mimeType == 'text/plain' && part.body?.data != null) {
          return _decodeBase64(part.body!.data!);
        }
      }
    }
    return "";
  }

  String _decodeBase64(String data) {
    try {
      final normalized = base64Url.normalize(data);
      return utf8.decode(base64Url.decode(normalized));
    } catch (e) {
      return "";
    }
  }

  Future<EmailAnalysisResult> analyzeEmail(GmailMessageHeader email) async {
    final data = await _ref.read(auraApiClientProvider).json(
      method: 'POST',
      path: '/api/v3/automation/analyze-email',
      body: {
        'subject': email.subject,
        'sender': email.sender,
        'body': email.body,
      },
    );
    return EmailAnalysisResult.fromMap(data);
  }

  // GitHub Integrations: Fetch assigned issues
  Future<List<GitHubIssueItem>> fetchGitHubIssues(String repoPath, String token) async {
    // repoPath format: "owner/repo"
    final url = Uri.parse("https://api.github.com/repos/$repoPath/issues?state=open");
    final response = await http.get(
      url,
      headers: {
        "Authorization": "token $token",
        "Accept": "application/vnd.github.v3+json",
      },
    );

    if (response.statusCode == 200) {
      final List list = jsonDecode(response.body);
      // Filter out pull requests (GitHub API lists pull requests as issues too)
      return list
          .where((item) => item['pull_request'] == null)
          .map((item) => GitHubIssueItem.fromMap(item as Map<String, dynamic>))
          .toList();
    } else {
      throw Exception("GitHub API returned error ${response.statusCode}: ${response.body}");
    }
  }

  // Call backend to analyze github issue
  Future<GitHubIssueResult> analyzeGitHubIssue(GitHubIssueItem issue) async {
    final data = await _ref.read(auraApiClientProvider).json(
      method: 'POST',
      path: '/api/v3/automation/analyze-issue',
      body: {
        'title': issue.title,
        'body': issue.body,
      },
    );
    return GitHubIssueResult.fromMap(data);
  }

  // Drive Integrations: Create Project Folder and Google Doc Workspace
  Future<List<String>> createProjectWorkspace({
    required String name,
    required String description,
  }) async {
    final logs = <String>[];
    logs.add(currentL10n.automationsDriveRequestingPermissions);

    final driveScopes = [
      'https://www.googleapis.com/auth/drive.readonly',
      'https://www.googleapis.com/auth/drive.file'
    ];
    
    // Acquire OAuth token and client
    final GoogleSignInClientAuthorization authorization =
        await GoogleSignIn.instance.authorizationClient.authorizeScopes(driveScopes);
    final client = authorization.authClient(scopes: driveScopes);

    final driveApi = drive.DriveApi(client);
    logs.add(currentL10n.automationsDriveConnected);

    // 1. Create Workspace Folder
    final folder = drive.File()
      ..name = "AURA Workspace - $name"
      ..mimeType = "application/vnd.google-apps.folder";

    final createdFolder = await driveApi.files.create(folder);
    if (createdFolder.id == null) {
      throw Exception("Failed to create Drive folder.");
    }
    logs.add(currentL10n.automationsDriveCreatedFolder("AURA Workspace - $name"));

    // 2. Create Project Doc inside the folder
    final docContent = """
# Project Plan: $name
*Created automatically by AURA AI OS*

## Project Description
$description

## Project Milestones & AI Checklist
- [ ] Phase 1: Setup Workspace & Project Requirements Scoping
- [ ] Phase 2: Design and Baseline Implementation
- [ ] Phase 3: Testing & Integration validations
- [ ] Phase 4: Delivery and Review

## General Guidelines
- Maintain all relevant project files within this Google Drive Folder.
- Use tasks and calendar events in AURA AI for scheduling milestones.
""";

    final documentFile = drive.File()
      ..name = "AURA Project Plan - $name"
      ..mimeType = "application/vnd.google-apps.document"
      ..parents = [createdFolder.id!];

    final media = drive.Media(
      Stream.value(utf8.encode(docContent)),
      docContent.length,
    );

    await driveApi.files.create(documentFile, uploadMedia: media);
    logs.add(currentL10n.automationsDriveCreatedDocument("AURA Project Plan - $name"));

    logs.add(currentL10n.automationsDriveDone);
    return logs;
  }
}

final automationRepositoryProvider = Provider<AutomationRepository>((ref) {
  return AutomationRepository(ref);
});
