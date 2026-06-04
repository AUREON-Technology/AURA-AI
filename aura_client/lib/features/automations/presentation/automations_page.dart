import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:extension_google_sign_in_as_googleapis_auth/extension_google_sign_in_as_googleapis_auth.dart';
import 'package:intl/intl.dart';
import '../data/automation_repository.dart';
import '../../auth/presentation/auth_provider.dart';
import '../../tasks/data/task_repository.dart';
import '../../calendar/data/calendar_repository.dart';
import '../../../core/theme/theme.dart';

class AutomationsPage extends ConsumerStatefulWidget {
  const AutomationsPage({super.key});

  @override
  ConsumerState<AutomationsPage> createState() => _AutomationsPageState();
}

class _AutomationsPageState extends ConsumerState<AutomationsPage> {
  int _activeTab = 0; // 0 = Gmail, 1 = GitHub, 2 = Drive

  // GitHub State Variables
  final _githubPatController = TextEditingController();
  final _githubRepoController = TextEditingController(text: 'Anas-Ibrahim/Aura-AI');
  bool _loadingGitHub = false;
  List<GitHubIssueItem> _githubIssues = [];
  final Map<int, GitHubIssueResult> _githubAnalyses = {};
  final Map<int, bool> _loadingIssueAnalysis = {};

  // Gmail State Variables
  bool _loadingGmail = false;
  List<GmailMessageHeader> _unreadEmails = [];
  final Map<String, EmailAnalysisResult> _emailAnalyses = {};
  final Map<String, bool> _loadingEmailAnalysis = {};

  // Drive State Variables
  final _projectNameController = TextEditingController();
  final _projectDescController = TextEditingController();
  bool _creatingWorkspace = false;
  List<String> _workspaceLogs = [];

  @override
  void initState() {
    super.initState();
    _loadGitHubPat();
  }

  @override
  void dispose() {
    _githubPatController.dispose();
    _githubRepoController.dispose();
    _projectNameController.dispose();
    _projectDescController.dispose();
    super.dispose();
  }

  void _loadGitHubPat() async {
    final token = await ref.read(automationRepositoryProvider).getGitHubToken();
    if (token != null) {
      setState(() {
        _githubPatController.text = token;
      });
    }
  }

  // ----------------------------------------------------
  // Gmail Analyzer Operations
  // ----------------------------------------------------
  Future<void> _fetchGmail() async {
    setState(() {
      _loadingGmail = true;
      _unreadEmails = [];
    });

    try {
      final googleSignIn = GoogleSignIn.instance;
      final scopes = ['https://www.googleapis.com/auth/gmail.readonly'];
      
      // Request scope access
      final GoogleSignInClientAuthorization authorization =
          await googleSignIn.authorizationClient.authorizeScopes(scopes);
      final client = authorization.authClient(scopes: scopes);

      final emails = await ref.read(automationRepositoryProvider).fetchUnreadEmails(client);
      setState(() {
        _unreadEmails = emails;
        _loadingGmail = false;
      });
    } catch (e) {
      setState(() => _loadingGmail = false);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Gmail Fetch Error: $e"), backgroundColor: Colors.redAccent),
        );
      }
    }
  }

  Future<void> _analyzeEmail(GmailMessageHeader email) async {
    setState(() {
      _loadingEmailAnalysis[email.id] = true;
    });

    try {
      final result = await ref.read(automationRepositoryProvider).analyzeEmail(email);
      setState(() {
        _emailAnalyses[email.id] = result;
        _loadingEmailAnalysis[email.id] = false;
      });
    } catch (e) {
      setState(() {
        _loadingEmailAnalysis[email.id] = false;
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Analysis failed: $e"), backgroundColor: Colors.redAccent),
        );
      }
    }
  }

  // ----------------------------------------------------
  // GitHub Board Operations
  // ----------------------------------------------------
  Future<void> _fetchGitHub() async {
    final token = _githubPatController.text.trim();
    var repo = _githubRepoController.text.trim();
    if (token.isEmpty || repo.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Please provide both GitHub PAT and Repository Path.")),
      );
      return;
    }

    // Clean up leading/trailing slashes
    while (repo.startsWith('/')) {
      repo = repo.substring(1);
    }
    while (repo.endsWith('/')) {
      repo = repo.substring(0, repo.length - 1);
    }

    // Validate format (must contain owner and repo separated by a single slash)
    final parts = repo.split('/');
    if (parts.length != 2 || parts[0].isEmpty || parts[1].isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text("Repository Path must be in 'owner/repo' format (e.g., owner/AURA-AI)."),
          backgroundColor: Colors.orangeAccent,
        ),
      );
      return;
    }

    // Update text field to show sanitized value
    _githubRepoController.text = repo;


    setState(() {
      _loadingGitHub = true;
      _githubIssues = [];
    });

    try {
      await ref.read(automationRepositoryProvider).saveGitHubToken(token);
      final issues = await ref.read(automationRepositoryProvider).fetchGitHubIssues(repo, token);
      setState(() {
        _githubIssues = issues;
        _loadingGitHub = false;
      });
    } catch (e) {
      setState(() => _loadingGitHub = false);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("GitHub fetch failed: $e"), backgroundColor: Colors.redAccent),
        );
      }
    }
  }

  Future<void> _analyzeGitHubIssue(GitHubIssueItem issue) async {
    setState(() {
      _loadingIssueAnalysis[issue.number] = true;
    });

    try {
      final result = await ref.read(automationRepositoryProvider).analyzeGitHubIssue(issue);
      setState(() {
        _githubAnalyses[issue.number] = result;
        _loadingIssueAnalysis[issue.number] = false;
      });
    } catch (e) {
      setState(() {
        _loadingIssueAnalysis[issue.number] = false;
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Issue analysis failed: $e"), backgroundColor: Colors.redAccent),
        );
      }
    }
  }

  // ----------------------------------------------------
  // Drive Workspace Builder Operations
  // ----------------------------------------------------
  Future<void> _generateWorkspace() async {
    final name = _projectNameController.text.trim();
    final desc = _projectDescController.text.trim();
    if (name.isEmpty || desc.isEmpty) return;

    setState(() {
      _creatingWorkspace = true;
      _workspaceLogs = ["Initializing project workspace folders..."];
    });

    try {
      final logs = await ref.read(automationRepositoryProvider).createProjectWorkspace(
            name: name,
            description: desc,
          );

      setState(() {
        _workspaceLogs = logs;
        _creatingWorkspace = false;
      });
      _projectNameController.clear();
      _projectDescController.clear();
    } catch (e) {
      setState(() {
        _workspaceLogs.add("Workspace creation failed: $e");
        _creatingWorkspace = false;
      });
    }
  }

  // ----------------------------------------------------
  // UI Builders
  // ----------------------------------------------------
  @override
  Widget build(BuildContext context) {
    final auth = ref.watch(authProvider).value;
    final isMock = auth?.id == 'mock_user_123' || auth == null;

    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: RadialGradient(
            center: Alignment(0.6, 0.6),
            radius: 1.5,
            colors: [
              Color(0xFF1E1B4B),
              Color(0xFF0F0C1B),
            ],
          ),
        ),
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(24.0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _buildHeader(),
                const SizedBox(height: 20),
                _buildTabs(),
                const SizedBox(height: 24),
                Expanded(
                  child: _activeTab == 0
                      ? _buildGmailTab(isMock)
                      : _activeTab == 1
                          ? _buildGitHubTab()
                          : _buildDriveTab(isMock),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildHeader() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          "AURA Automations",
          style: Theme.of(context).textTheme.displayLarge?.copyWith(fontSize: 28),
        ),
        const SizedBox(height: 4),
        const Text(
          "Extract tasks from emails and issues, generate workspace structures",
          style: TextStyle(color: AuraTheme.textSecondary, fontSize: 14),
        ),
      ],
    );
  }

  Widget _buildTabs() {
    return Row(
      children: [
        _buildTabItem(index: 0, label: "Gmail Inbox Analyzer", icon: Icons.mail_outline),
        const SizedBox(width: 12),
        _buildTabItem(index: 1, label: "GitHub Issue Importer", icon: Icons.code),
        const SizedBox(width: 12),
        _buildTabItem(index: 2, label: "Drive Workspace Builder", icon: Icons.cloud_queue),
      ],
    );
  }

  Widget _buildTabItem({required int index, required String label, required IconData icon}) {
    final isActive = _activeTab == index;

    return GestureDetector(
      onTap: () => setState(() => _activeTab = index),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: isActive ? AuraTheme.accentIndigo.withValues(alpha: 0.15) : AuraTheme.cardBg,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: isActive ? AuraTheme.accentIndigo : const Color(0x1F94A3B8),
            width: 1,
          ),
        ),
        child: Row(
          children: [
            Icon(icon, color: isActive ? AuraTheme.accentIndigo : AuraTheme.textSecondary, size: 16),
            const SizedBox(width: 8),
            Text(
              label,
              style: TextStyle(
                color: isActive ? Colors.white : AuraTheme.textSecondary,
                fontWeight: isActive ? FontWeight.bold : FontWeight.normal,
                fontSize: 13,
              ),
            ),
          ],
        ),
      ),
    );
  }

  // ----------------------------------------------------
  // 1. Gmail Tab Render
  // ----------------------------------------------------
  Widget _buildGmailTab(bool isMock) {
    if (isMock) {
      return const Card(
        child: Padding(
          padding: EdgeInsets.all(24.0),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.lock_outline, size: 64, color: AuraTheme.accentAmber),
              SizedBox(height: 16),
              Text(
                "Live Gmail Integration Unavailable",
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: Colors.white),
              ),
              SizedBox(height: 8),
              Text(
                "You are signed in via a mock developer profile. Real unread emails can only be fetched when logged in with a real Google Account.",
                textAlign: TextAlign.center,
                style: TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
              ),
            ],
          ),
        ),
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            const Text(
              "Recent Unread Emails",
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.white),
            ),
            _loadingGmail
                ? const SizedBox(
                    width: 24,
                    height: 24,
                    child: CircularProgressIndicator(strokeWidth: 2, color: AuraTheme.accentIndigo),
                  )
                : ElevatedButton.icon(
                    onPressed: _fetchGmail,
                    icon: const Icon(Icons.refresh),
                    label: const Text("Fetch Unread"),
                  ),
          ],
        ),
        const SizedBox(height: 16),
        Expanded(
          child: _unreadEmails.isEmpty
              ? Center(
                  child: Text(
                    _loadingGmail ? "Fetching..." : "No unread emails loaded. Click Fetch.",
                    style: const TextStyle(color: AuraTheme.textSecondary),
                  ),
                )
              : ListView.builder(
                  itemCount: _unreadEmails.length,
                  itemBuilder: (context, index) {
                    final email = _unreadEmails[index];
                    final isAnalyzing = _loadingEmailAnalysis[email.id] ?? false;
                    final analysis = _emailAnalyses[email.id];

                    return Card(
                      margin: const EdgeInsets.only(bottom: 16),
                      child: Padding(
                        padding: const EdgeInsets.all(16.0),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            Row(
                              children: [
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        email.subject,
                                        style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15),
                                      ),
                                      const SizedBox(height: 4),
                                      Text(
                                        "From: ${email.sender}",
                                        style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 12),
                                      ),
                                    ],
                                  ),
                                ),
                                if (analysis == null)
                                  isAnalyzing
                                      ? const CircularProgressIndicator(color: AuraTheme.accentIndigo)
                                      : OutlinedButton.icon(
                                          onPressed: () => _analyzeEmail(email),
                                          icon: const Icon(Icons.auto_awesome, size: 14),
                                          label: const Text("AI Analyze"),
                                        ),
                              ],
                            ),
                            const Divider(color: Color(0x1F94A3B8), height: 24),
                            if (analysis == null)
                              Text(
                                email.snippet,
                                style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
                              )
                            else
                              _buildEmailAnalysisWidget(email.id, analysis),
                          ],
                        ),
                      ),
                    );
                  },
                ),
        ),
      ],
    );
  }

  Widget _buildEmailAnalysisWidget(String emailId, EmailAnalysisResult analysis) {
    final isUrgent = analysis.urgency.toLowerCase() == 'high';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Category and Urgency row
        Row(
          children: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                color: AuraTheme.accentIndigo.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                "Category: ${analysis.category}",
                style: const TextStyle(color: AuraTheme.accentIndigo, fontSize: 11, fontWeight: FontWeight.bold),
              ),
            ),
            const SizedBox(width: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                color: isUrgent ? Colors.redAccent.withValues(alpha: 0.15) : AuraTheme.accentTeal.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                isUrgent ? "URGENT" : "Low Urgency",
                style: TextStyle(color: isUrgent ? Colors.redAccent : AuraTheme.accentTeal, fontSize: 11, fontWeight: FontWeight.bold),
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        // AI Summary
        const Text("AI Summary", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 12, color: Colors.white)),
        const SizedBox(height: 4),
        Text(
          analysis.summary,
          style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
        ),
        const SizedBox(height: 16),
        // Suggested Tasks
        if (analysis.actionItems.isNotEmpty) ...[
          const Text("Action Items Extracted", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 12, color: Colors.white)),
          const SizedBox(height: 8),
          ...analysis.actionItems.map((item) {
            return Container(
              margin: const EdgeInsets.only(bottom: 8),
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: const Color(0x0AFFFFFF),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(item.title, style: const TextStyle(fontSize: 13)),
                        if (item.dueDate != null)
                          Text(
                            "Suggested Deadline: ${DateFormat('yyyy-MM-dd').format(item.dueDate!)}",
                            style: const TextStyle(color: AuraTheme.accentAmber, fontSize: 11),
                          ),
                      ],
                    ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.playlist_add, color: AuraTheme.accentTeal),
                    tooltip: "Import as AURA Task",
                    onPressed: () async {
                      final messenger = ScaffoldMessenger.of(context);
                      await ref.read(taskRepositoryProvider).addTask(
                            title: item.title,
                            priority: isUrgent ? 'high' : 'medium',
                            category: analysis.category.toLowerCase(),
                            dueDate: item.dueDate,
                          );
                      messenger.showSnackBar(
                        SnackBar(content: Text("Imported task: ${item.title}")),
                      );
                    },
                  ),
                ],
              ),
            );
          }),
        ],
        // Suggested Event
        if (analysis.shouldCreateEvent && analysis.eventTitle != null && analysis.eventStart != null) ...[
          const SizedBox(height: 12),
          const Text("Suggested Calendar Event", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 12, color: Colors.white)),
          const SizedBox(height: 8),
          Container(
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(
              color: AuraTheme.accentIndigo.withValues(alpha: 0.1),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: AuraTheme.accentIndigo.withValues(alpha: 0.3)),
            ),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(analysis.eventTitle!, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.bold)),
                      const SizedBox(height: 2),
                      Text(
                        "${DateFormat('yyyy-MM-dd HH:mm').format(analysis.eventStart!)} - ${DateFormat('HH:mm').format(analysis.eventEnd ?? analysis.eventStart!.add(const Duration(hours: 1)))}",
                        style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 11),
                      ),
                    ],
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.event, color: AuraTheme.accentIndigo),
                  tooltip: "Schedule Event",
                  onPressed: () async {
                    final messenger = ScaffoldMessenger.of(context);
                    await ref.read(calendarRepositoryProvider).addEvent(
                          title: analysis.eventTitle!,
                          startTime: analysis.eventStart!,
                          endTime: analysis.eventEnd ?? analysis.eventStart!.add(const Duration(hours: 1)),
                        );
                    messenger.showSnackBar(
                      SnackBar(content: Text("Scheduled event: ${analysis.eventTitle}")),
                    );
                  },
                ),
              ],
            ),
          ),
        ],
      ],
    );
  }

  // ----------------------------------------------------
  // 2. GitHub Tab Render
  // ----------------------------------------------------
  Widget _buildGitHubTab() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16.0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text("GitHub Integration Setup", style: TextStyle(fontWeight: FontWeight.bold)),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                      flex: 3,
                      child: TextField(
                        controller: _githubPatController,
                        obscureText: true,
                        decoration: const InputDecoration(
                          labelText: 'GitHub PAT (Personal Access Token)',
                          hintText: 'ghp_...',
                        ),
                        style: const TextStyle(color: Colors.white),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      flex: 2,
                      child: TextField(
                        controller: _githubRepoController,
                        decoration: const InputDecoration(
                          labelText: 'Repository Path',
                          hintText: 'owner/repo',
                        ),
                        style: const TextStyle(color: Colors.white),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                ElevatedButton.icon(
                  onPressed: _fetchGitHub,
                  icon: const Icon(Icons.search),
                  label: const Text("Load Repo Issues"),
                  style: ElevatedButton.styleFrom(backgroundColor: AuraTheme.accentIndigo),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 20),
        Expanded(
          child: _githubIssues.isEmpty
              ? Center(
                  child: Text(
                    _loadingGitHub ? "Loading GitHub Board..." : "No open issues loaded.",
                    style: const TextStyle(color: AuraTheme.textSecondary),
                  ),
                )
              : ListView.builder(
                  itemCount: _githubIssues.length,
                  itemBuilder: (context, index) {
                    final issue = _githubIssues[index];
                    final isAnalyzing = _loadingIssueAnalysis[issue.number] ?? false;
                    final analysis = _githubAnalyses[issue.number];

                    return Card(
                      margin: const EdgeInsets.only(bottom: 16),
                      child: Padding(
                        padding: const EdgeInsets.all(16.0),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            Row(
                              children: [
                                Expanded(
                                  child: Text(
                                    "#${issue.number} - ${issue.title}",
                                    style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14),
                                  ),
                                ),
                                if (analysis == null)
                                  isAnalyzing
                                      ? const CircularProgressIndicator(color: AuraTheme.accentIndigo)
                                      : OutlinedButton.icon(
                                          onPressed: () => _analyzeGitHubIssue(issue),
                                          icon: const Icon(Icons.analytics_outlined, size: 14),
                                          label: const Text("Extract Subtasks"),
                                        ),
                              ],
                            ),
                            const Divider(color: Color(0x1F94A3B8), height: 20),
                            if (analysis == null)
                              Text(
                                issue.body.length > 180 ? "${issue.body.substring(0, 180)}..." : issue.body,
                                style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
                              )
                            else
                              _buildGitHubAnalysisWidget(issue, analysis),
                          ],
                        ),
                      ),
                    );
                  },
                ),
        ),
      ],
    );
  }

  Widget _buildGitHubAnalysisWidget(GitHubIssueItem issue, GitHubIssueResult analysis) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                color: AuraTheme.accentAmber.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                "Complexity: ${analysis.complexity}",
                style: const TextStyle(color: AuraTheme.accentAmber, fontSize: 11, fontWeight: FontWeight.bold),
              ),
            ),
            const SizedBox(width: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                color: AuraTheme.accentTeal.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                "Priority: ${analysis.suggestedPriority}",
                style: const TextStyle(color: AuraTheme.accentTeal, fontSize: 11, fontWeight: FontWeight.bold),
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        const Text("Issue Summary", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 12)),
        const SizedBox(height: 4),
        Text(analysis.summary, style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 13)),
        const SizedBox(height: 16),
        if (analysis.subtasks.isNotEmpty) ...[
          const Text("Extracted Coding Tasks", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 12)),
          const SizedBox(height: 8),
          ...analysis.subtasks.map((task) {
            return Container(
              margin: const EdgeInsets.only(bottom: 8),
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: const Color(0x0AFFFFFF),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Row(
                children: [
                  Expanded(child: Text(task, style: const TextStyle(fontSize: 13))),
                  IconButton(
                    icon: const Icon(Icons.add_task, color: AuraTheme.accentTeal),
                    onPressed: () async {
                      final messenger = ScaffoldMessenger.of(context);
                      await ref.read(taskRepositoryProvider).addTask(
                            title: "GitHub #${issue.number}: $task",
                            priority: analysis.suggestedPriority,
                            category: 'project',
                          );
                      messenger.showSnackBar(
                        const SnackBar(content: Text("Imported coding task!")),
                      );
                    },
                  ),
                ],
              ),
            );
          }),
        ],
      ],
    );
  }

  // ----------------------------------------------------
  // 3. Drive Tab Render
  // ----------------------------------------------------
  Widget _buildDriveTab(bool isMock) {
    if (isMock) {
      return const Card(
        child: Padding(
          padding: EdgeInsets.all(24.0),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.lock_outline, size: 64, color: AuraTheme.accentAmber),
              SizedBox(height: 16),
              Text(
                "Google Drive Workspace Creation Unavailable",
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: Colors.white),
              ),
              SizedBox(height: 8),
              Text(
                "You are signed in via a mock developer profile. Drive workspace directories can only be generated when logged in with a real Google Account.",
                textAlign: TextAlign.center,
                style: TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
              ),
            ],
          ),
        ),
      );
    }

    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text("Drive Project Workspace Generator", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
                  const SizedBox(height: 4),
                  const Text(
                    "Enter project details. AURA will generate a Google Drive folder and populate it with a structured Markdown project outline document.",
                    style: TextStyle(color: AuraTheme.textSecondary, fontSize: 12),
                  ),
                  const Divider(color: Color(0x1F94A3B8), height: 32),
                  TextField(
                    controller: _projectNameController,
                    decoration: const InputDecoration(
                      labelText: 'Project Name',
                      hintText: 'e.g. Freelance Mobile App',
                    ),
                    style: const TextStyle(color: Colors.white),
                  ),
                  const SizedBox(height: 16),
                  TextField(
                    controller: _projectDescController,
                    maxLines: 4,
                    decoration: const InputDecoration(
                      labelText: 'Project Description / Outline',
                      hintText: 'Describe details to generate template plan',
                    ),
                    style: const TextStyle(color: Colors.white),
                  ),
                  const SizedBox(height: 20),
                  _creatingWorkspace
                      ? const Center(child: CircularProgressIndicator(color: AuraTheme.accentIndigo))
                      : ElevatedButton.icon(
                          onPressed: _generateWorkspace,
                          icon: const Icon(Icons.create_new_folder),
                          label: const Text("Generate Drive Workspace"),
                          style: ElevatedButton.styleFrom(backgroundColor: AuraTheme.accentTeal),
                        ),
                ],
              ),
            ),
          ),
          if (_workspaceLogs.isNotEmpty) ...[
            const SizedBox(height: 20),
            Card(
              color: const Color(0xFF07040D),
              child: Padding(
                padding: const EdgeInsets.all(16.0),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text("Automation Activity Console", style: TextStyle(color: AuraTheme.accentIndigo, fontWeight: FontWeight.bold, fontSize: 12)),
                    const SizedBox(height: 8),
                    ..._workspaceLogs.map((log) => Padding(
                          padding: const EdgeInsets.symmetric(vertical: 4.0),
                          child: Text(
                            log,
                            style: const TextStyle(fontFamily: 'Courier', fontSize: 12, color: Colors.greenAccent),
                          ),
                        )),
                  ],
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}
