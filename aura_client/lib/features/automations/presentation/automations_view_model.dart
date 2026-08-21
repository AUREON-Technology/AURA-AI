import 'package:flutter_riverpod/flutter_riverpod.dart';

class AutomationsViewModel extends Notifier<int> {
  @override
  int build() => 0;

  void selectTab(int index) {
    if (index < 0 || index > 2) return;
    state = index;
  }
}

final automationsViewModelProvider = NotifierProvider<AutomationsViewModel, int>(AutomationsViewModel.new);
