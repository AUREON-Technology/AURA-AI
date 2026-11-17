import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

import 'onboarding_gate.dart';

class OnboardingPage extends ConsumerStatefulWidget {
  const OnboardingPage({super.key});

  @override
  ConsumerState<OnboardingPage> createState() => _OnboardingPageState();
}

class _OnboardingPageState extends ConsumerState<OnboardingPage> {
  final _controller = PageController();
  int _index = 0;

  static const _pages = [
    (
      Icons.auto_awesome_outlined,
      'AURA is your copilot',
      'Talk to it. It remembers, searches your documents, and proposes actions — you confirm the writes.',
    ),
    (
      Icons.cloud_outlined,
      'Your data stays on your server',
      'Inference runs on AURA’s own machines. Nothing is sent to a third-party AI service, and nothing is processed on this phone.',
    ),
    (
      Icons.view_week_outlined,
      'Today, Copilot, Life Hub, You',
      'Capture from anywhere. Browse tasks, calendar, finance, and health in Life Hub. Account tools live under You.',
    ),
  ];

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _finish() async {
    await ref.read(onboardingGateProvider.notifier).complete();
    if (mounted) context.go('/today');
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Scaffold(
      backgroundColor: palette.background,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(AuraSpacing.xl),
          child: Column(
            children: [
              Align(
                alignment: AlignmentDirectional.centerEnd,
                child: AnimatedOpacity(
                  opacity: _index == _pages.length - 1 ? 0 : 1,
                  duration: AuraMotion.respecting(context, AuraMotion.fast),
                  child: TextButton(
                    onPressed: _index == _pages.length - 1 ? null : _finish,
                    child: Text(context.l10n.onboardingSkip),
                  ),
                ),
              ),
              Expanded(
                child: PageView.builder(
                  controller: _controller,
                  itemCount: _pages.length,
                  onPageChanged: (i) => setState(() => _index = i),
                  itemBuilder: (context, i) {
                    final page = _pages[i];
                    return Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        ExcludeSemantics(
                          child: Container(
                            width: 112,
                            height: 112,
                            decoration: BoxDecoration(
                              gradient: palette.brandGradient,
                              borderRadius: BorderRadius.circular(
                                AuraRadius.xl + 8,
                              ),
                              boxShadow: [
                                BoxShadow(
                                  color: palette.brand.withValues(alpha: 0.35),
                                  blurRadius: 36,
                                  offset: const Offset(0, 14),
                                ),
                              ],
                            ),
                            child: Icon(
                              page.$1,
                              size: 52,
                              color: palette.onBrand,
                            ),
                          ),
                        ),
                        const SizedBox(height: AuraSpacing.xxl),
                        Text(
                          page.$2,
                          style: Theme.of(context).textTheme.headlineMedium
                              ?.copyWith(fontWeight: FontWeight.w700),
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: AuraSpacing.md),
                        ConstrainedBox(
                          constraints: const BoxConstraints(maxWidth: 420),
                          child: Text(
                            page.$3,
                            style: Theme.of(context).textTheme.bodyLarge
                                ?.copyWith(
                                  color: palette.textSecondary,
                                  height: 1.5,
                                ),
                            textAlign: TextAlign.center,
                          ),
                        ),
                      ],
                    );
                  },
                ),
              ),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  for (var i = 0; i < _pages.length; i++)
                    AnimatedContainer(
                      duration: AuraMotion.respecting(
                        context,
                        AuraMotion.standard,
                      ),
                      curve: AuraMotion.standardCurve,
                      width: i == _index ? 24 : 8,
                      height: 8,
                      margin: const EdgeInsets.symmetric(horizontal: 4),
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(AuraRadius.pill),
                        gradient: i == _index ? palette.brandGradient : null,
                        color: i == _index ? null : palette.border,
                      ),
                    ),
                ],
              ),
              const SizedBox(height: AuraSpacing.xl),
              AuraButton(
                label: _index == _pages.length - 1
                    ? context.l10n.onboardingGetStarted
                    : context.l10n.onboardingNext,
                expand: true,
                onPressed: () {
                  if (_index == _pages.length - 1) {
                    _finish();
                  } else {
                    _controller.nextPage(
                      duration: AuraMotion.respecting(
                        context,
                        AuraMotion.standard,
                      ),
                      curve: AuraMotion.standardCurve,
                    );
                  }
                },
              ),
            ],
          ),
        ),
      ),
    );
  }
}
