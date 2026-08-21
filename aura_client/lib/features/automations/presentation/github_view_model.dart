import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../tasks/data/task_repository.dart';
import '../data/automation_repository.dart';

/// Returns a sanitized `owner/repo` path, or null if the input is invalid.
String? sanitizeGitHubRepoPath(String raw) {
  var repo = raw.trim();
  while (repo.startsWith('/')) {
    repo = repo.substring(1);
  }
  while (repo.endsWith('/')) {
    repo = repo.substring(0, repo.length - 1);
  }
  final parts = repo.split('/');
  if (parts.length != 2 || parts[0].isEmpty || parts[1].isEmpty) return null;
  return repo;
}

@immutable
class GitHubUiState {
  final bool loading;
  final List<GitHubIssueItem> issues;
  final Map<int, GitHubIssueResult> analyses;
  final Set<int> analyzingIds;
  final String? savedPat;

  const GitHubUiState({
    this.loading = false,
    this.issues = const [],
    this.analyses = const {},
    this.analyzingIds = const {},
    this.savedPat,
  });

  GitHubUiState copyWith({
    bool? loading,
    List<GitHubIssueItem>? issues,
    Map<int, GitHubIssueResult>? analyses,
    Set<int>? analyzingIds,
    String? savedPat,
  }) {
    return GitHubUiState(
      loading: loading ?? this.loading,
      issues: issues ?? this.issues,
      analyses: analyses ?? this.analyses,
      analyzingIds: analyzingIds ?? this.analyzingIds,
      savedPat: savedPat ?? this.savedPat,
    );
  }
}

class GitHubViewModel extends Notifier<GitHubUiState> {
  @override
  GitHubUiState build() {
    Future.microtask(_loadSavedPat);
    return const GitHubUiState();
  }

  Future<void> _loadSavedPat() async {
    final token = await ref.read(automationRepositoryProvider).getGitHubToken();
    if (token != null) {
      state = state.copyWith(savedPat: token);
    }
  }

  Future<String?> fetchIssues({required String token, required String repoRaw}) async {
    if (token.trim().isEmpty) {
      return 'Please provide both GitHub PAT and Repository Path.';
    }
    final repo = sanitizeGitHubRepoPath(repoRaw);
    if (repo == null) {
      return "Repository Path must be in 'owner/repo' format (e.g., owner/AURA-AI).";
    }

    state = state.copyWith(loading: true, issues: const []);
    try {
      await ref.read(automationRepositoryProvider).saveGitHubToken(token.trim());
      final issues = await ref.read(automationRepositoryProvider).fetchGitHubIssues(repo, token.trim());
      state = state.copyWith(loading: false, issues: issues);
      return null;
    } catch (e) {
      state = state.copyWith(loading: false);
      return 'GitHub fetch failed: $e';
    }
  }

  Future<String?> analyzeIssue(GitHubIssueItem issue) async {
    state = state.copyWith(analyzingIds: {...state.analyzingIds, issue.number});
    try {
      final result = await ref.read(automationRepositoryProvider).analyzeGitHubIssue(issue);
      final analyses = Map<int, GitHubIssueResult>.from(state.analyses)..[issue.number] = result;
      final analyzing = Set<int>.from(state.analyzingIds)..remove(issue.number);
      state = state.copyWith(analyses: analyses, analyzingIds: analyzing);
      return null;
    } catch (e) {
      final analyzing = Set<int>.from(state.analyzingIds)..remove(issue.number);
      state = state.copyWith(analyzingIds: analyzing);
      return 'Issue analysis failed: $e';
    }
  }

  Future<void> importSubtask(GitHubIssueItem issue, GitHubIssueResult analysis, String task) {
    return ref.read(taskRepositoryProvider).addTask(
          title: 'GitHub #${issue.number}: $task',
          priority: analysis.suggestedPriority,
          category: 'project',
        );
  }
}

final gitHubViewModelProvider = NotifierProvider<GitHubViewModel, GitHubUiState>(GitHubViewModel.new);
