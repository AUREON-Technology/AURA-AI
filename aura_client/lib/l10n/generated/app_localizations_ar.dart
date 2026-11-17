// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Arabic (`ar`).
class AppLocalizationsAr extends AppLocalizations {
  AppLocalizationsAr([String locale = 'ar']) : super(locale);

  @override
  String get greetingMorning => 'صباح الخير';

  @override
  String get greetingAfternoon => 'نهارك سعيد';

  @override
  String get greetingEvening => 'مساء الخير';

  @override
  String get captureBarHint => 'اسأل AURA أي حاجة، أو سجّل مهمة…';

  @override
  String get dueToday => 'مطلوب النهارده';

  @override
  String get viewAll => 'عرض الكل';

  @override
  String get nothingDueToday => 'مفيش حاجة مطلوبة النهارده';

  @override
  String get allCaughtUp => 'إنت مخلّص كل حاجة.';

  @override
  String get couldNotLoadDueToday => 'مقدرناش نحمّل حاجات النهارده';

  @override
  String get activeTasks => 'المهام الشغالة';

  @override
  String get waterToday => 'المية النهارده';

  @override
  String get spentThisPeriod => 'المصروف في الفترة دي';

  @override
  String get aiSystemStatus => 'حالة نظام الذكاء الاصطناعي';

  @override
  String get serverInference => 'خدمة AURA AI';

  @override
  String get statusReady => 'جاهز';

  @override
  String get statusUnavailable => 'مش متاح';

  @override
  String get localData => 'البيانات المحلية';

  @override
  String get encryptedAesGcm => 'متشفرة (AES-256-GCM)';

  @override
  String get settingsTooltip => 'أنا';

  @override
  String get navToday => 'النهارده';

  @override
  String get navCopilot => 'Copilot';

  @override
  String get navLifeHub => 'حياتي';

  @override
  String get navYou => 'أنا';

  @override
  String get lifeHubTitle => 'حياتي';

  @override
  String get youTitle => 'أنا';

  @override
  String get knowledgeTitle => 'المعرفة';

  @override
  String get memoryTitle => 'الذاكرة';

  @override
  String get feedbackTitle => 'الملاحظات';

  @override
  String get appearanceTitle => 'المظهر';

  @override
  String get engineStatusTitle => 'حالة المحرك';

  @override
  String get onboardingNext => 'التالي';

  @override
  String get onboardingCopilotTitle => 'AURA هو الـ Copilot بتاعك';

  @override
  String get onboardingCopilotBody =>
      'كلّمه. هو بيفتكر، وبيدوّر في مستنداتك، وبيقترح إجراءات — وإنت اللي بتأكد أي تعديل.';

  @override
  String get onboardingPrivacyTitle => 'بياناتك الخاصة بتفضل مع AURA';

  @override
  String get onboardingPrivacyBody =>
      'مفيش حاجة بتتعالج على الموبايل ده. مستنداتك وذكرياتك وملفاتك مبيقراهاش غير نماذج ذكاء اصطناعي AURA مشغّلها بنفسه. الرسايل التانية ممكن يرد عليها مزوّد ذكاء اصطناعي خارجي معتمد، لو مشغّل السيرفر سامح بكده.';

  @override
  String get onboardingTourTitle => 'النهارده، Copilot، حياتي، أنا';

  @override
  String get onboardingTourBody =>
      'سجّل من أي مكان. تصفّح المهام والتقويم والماليات والصحة في «حياتي». أدوات الحساب موجودة تحت «أنا».';

  @override
  String get onboardingSkip => 'تخطّي';

  @override
  String get onboardingGetStarted => 'يلا نبدأ';

  @override
  String get notificationsTooltip => 'الإشعارات';

  @override
  String unreadNotifications(int count) {
    return 'إشعارات مش مقروءة: $count';
  }

  @override
  String get settingsSectionAccount => 'الحساب';

  @override
  String get settingsSectionDesktop => 'الديسكتوب';

  @override
  String get settingsSectionYourAura => 'AURA بتاعك';

  @override
  String get settingsSectionEngineer => 'المهندس';

  @override
  String get settingsSectionSystem => 'النظام';

  @override
  String get settingsSectionAbout => 'عن التطبيق';

  @override
  String get settingsProfileTitle => 'البروفايل';

  @override
  String get settingsProfileSubtitle => 'الحساب المسجّل دخوله والأمان';

  @override
  String get settingsAppearanceSubtitle => 'زي الجهاز، فاتح، أو غامق';

  @override
  String get serverEnvironmentTitle => 'بيئة السيرفر';

  @override
  String get settingsServerEnvironmentSubtitle => 'سيرفر الإنتاج أو التطوير';

  @override
  String get settingsDevicesSubtitle =>
      'تسجيلات الدخول من الديسكتوب والـ Daemons المربوطة بالمهندس';

  @override
  String get devicesTitle => 'أجهزة الديسكتوب';

  @override
  String get devicesIntro =>
      'كل تطبيق ديسكتوب مسجّل دخول على حسابك وكل Daemon للمهندس مربوط بيه. الغي أي واحد عشان يتسجّل خروجه على طول.';

  @override
  String get devicesEmpty => 'مفيش أجهزة ديسكتوب';

  @override
  String get devicesEmptyMessage =>
      'سجّل دخول من تطبيق الديسكتوب أو اربط Daemon للمهندس وهيظهر هنا.';

  @override
  String get devicesLoadFailed => 'مقدرناش نحمّل أجهزتك';

  @override
  String get devicesRetry => 'جرّب تاني';

  @override
  String get devicesUnnamed => 'جهاز من غير اسم';

  @override
  String get devicesKindApp => 'تطبيق ديسكتوب';

  @override
  String get devicesKindDaemon => 'Daemon المهندس';

  @override
  String get devicesKindUnknown => 'جهاز';

  @override
  String devicesPairedOn(String date) {
    return 'من $date';
  }

  @override
  String get devicesThisDevice => 'الجهاز ده';

  @override
  String get devicesRevoke => 'إلغاء الوصول';

  @override
  String get devicesKeep => 'سيبه';

  @override
  String get devicesRevokeConfirmTitle => 'عايز تلغي وصول الجهاز ده؟';

  @override
  String devicesRevokeConfirmMessage(String name) {
    return '$name هيتسجّل خروجه فورًا، ولازم يسجّل دخول أو يتربط تاني.';
  }

  @override
  String get devicesRevoked => 'اتلغى وصول الجهاز.';

  @override
  String get devicesRevokeFailed => 'مقدرناش نلغي وصول الجهاز ده. جرّب تاني.';

  @override
  String get settingsKnowledgeSubtitle =>
      'المستندات اللي Copilot يقدر يدوّر فيها';

  @override
  String get settingsMemorySubtitle => 'معلومات متصنّفة AURA فاكرها';

  @override
  String get settingsFeedbackSubtitle => 'التصحيحات اللي بعتّها';

  @override
  String get settingsEngineerTitle => 'المهندس';

  @override
  String get settingsEngineerSubtitle =>
      'مساحات الشغل، والـ PRs، وتشغيلات الـ Sandbox';

  @override
  String get settingsGithubTitle => 'ربط GitHub';

  @override
  String get settingsGithubSubtitle => 'اربط حساب GitHub بتاعك';

  @override
  String get settingsEngineStatusSubtitle => 'هل سيرفر AURA متاح ولا لأ';

  @override
  String get settingsAutomationsTitle => 'الأتمتة';

  @override
  String get settingsAutomationsSubtitle => 'مساعدين Gmail وGitHub وDrive';

  @override
  String get settingsUsageTitle => 'الاستخدام على الجهاز ده';

  @override
  String get settingsUsageSubtitle => 'زمن الاستجابة المحلي وعدد الطلبات';

  @override
  String get settingsDesktopIdeTitle => 'IDE الديسكتوب';

  @override
  String get settingsDesktopIdeOn => 'AURA بيفتح واجهة الـ IDE على الجهاز ده';

  @override
  String get settingsDesktopIdeOff =>
      'AURA بيفتح الشاشة الرئيسية العادية على الجهاز ده';

  @override
  String get settingsAboutTitle => 'AURA بيشتغل فين';

  @override
  String get settingsAboutBody =>
      'الطلبات بتتعالج على سيرفرات AURA، عمرها ما بتتعالج على الجهاز ده. مستنداتك وذكرياتك وملفاتك ونتايج الأدوات مبيقراهاش غير نماذج ذكاء اصطناعي AURA مشغّلها بنفسه. لو مشغّل السيرفر فعّل الخاصية دي، رسايلك التانية ممكن يرد عليها مزوّد ذكاء اصطناعي خارجي معتمد.';

  @override
  String get themeSystem => 'زي الجهاز';

  @override
  String get themeLight => 'فاتح';

  @override
  String get themeDark => 'غامق';

  @override
  String get themeSystemSubtitle => 'نفس إعداد الجهاز';

  @override
  String get themeLightSubtitle => 'فاتح على طول';

  @override
  String get themeDarkSubtitle => 'غامق على طول';

  @override
  String get serverSwitchedSignInAgain => 'سجّل دخول تاني عشان تكمّل.';

  @override
  String get serverCurrent => 'السيرفر الحالي';

  @override
  String get serverReachable => 'متاح';

  @override
  String get serverUnreachable => 'مفيش اتصال';

  @override
  String get serverChecking => 'جاري التحقق…';

  @override
  String get serverReachabilityError => 'مقدرناش نتأكد من الاتصال';

  @override
  String get deleteAccountTitle => 'مسح الحساب';

  @override
  String get deleteAccountSubtitle => 'امسح حسابك وبياناته نهائيًا';

  @override
  String get deleteAccountConfirmTitle => 'عايز تمسح حسابك؟';

  @override
  String get deleteAccountConfirmBody =>
      'ده هيمسح حساب AURA بتاعك من السيرفر نهائيًا: الذاكرة، والمستندات، والمهام وباقي السجلات المتزامنة، والمحادثات، وكل جهاز مسجّل دخول. مفيش رجوع في الخطوة دي. بعدها AURA هيسجّل خروجك ويمسح بياناته من الجهاز ده. لو عايز تمشي من غير ما تمسح أي حاجة، سجّل خروج بس.';

  @override
  String get deleteAccountConfirm => 'مسح';

  @override
  String get deleteAccountDone => 'حسابك اتمسح.';

  @override
  String get deleteAccountPartial =>
      'جلساتك اتقفلت، بس جزء من حسابك لسه متمسحش. سجّل دخول واختار «مسح الحساب» تاني عشان تكمّل.';

  @override
  String get deleteAccountFailed => 'حسابك متمسحش.';

  @override
  String get serverSwitchConfirmTitle => 'عايز تغيّر السيرفر؟';

  @override
  String get serverSwitchConfirmBody =>
      'حسابك وبياناتك موجودين على السيرفر اللي إنت سايبه. AURA هيرفع التغييرات اللي لسه متزامنتش عليه الأول لو قدر، وبعدين يسجّل خروجك ويمسح بياناته من الجهاز ده. سجّل دخول تاني على السيرفر الجديد.';

  @override
  String get serverSwitchConfirm => 'تغيير';

  @override
  String get serverSwitchExplainer =>
      'تغيير السيرفر بيمسح جلستك على الجهاز ده. سجّل دخول تاني بعد ما تغيّر البيئة.';

  @override
  String get appName => 'AURA AI';

  @override
  String get cancel => 'إلغاء';

  @override
  String get signOut => 'تسجيل الخروج';

  @override
  String get signOutConfirmTitle => 'عايز تسجّل خروج؟';

  @override
  String get signOutConfirmBody =>
      'AURA بيرفع التغييرات اللي لسه متزامنتش الأول لو قدر يوصل للسيرفر، وبعدين يمسح بياناته من الجهاز ده. التغييرات اللي مقدرش يرفعها هتضيع.';

  @override
  String get loginBiometricReason =>
      'أكّد هويتك عشان تفتح بروفايل AURA AI بتاعك';

  @override
  String get loginTagline => 'نظام تشغيل شخصي بالذكاء الاصطناعي';

  @override
  String get loginWelcomeBack => 'أهلًا بيك تاني';

  @override
  String get loginSubtitle =>
      'سجّل دخول عشان تزامن مواعيدك ومذاكرتك ومساحة شغلك والـ Agents بتوعك.';

  @override
  String get loginSessionExpired =>
      'جلستك خلصت. سجّل دخول تاني عشان تكمّل من مكان ما وقفت — كل حاجة على الجهاز ده لسه موجودة.';

  @override
  String get loginGoogle => 'تسجيل الدخول بـ Google';

  @override
  String get loginGoogleBrowser => 'تسجيل الدخول بـ Google (من المتصفح)';

  @override
  String get loginUnlockBiometrics => 'افتح بالبصمة';

  @override
  String get loginShowDeveloperPanel => 'دخول المطوّرين / من غير اتصال';

  @override
  String get loginHideDeveloperPanel => 'إخفاء لوحة المطوّرين';

  @override
  String get loginDeveloperName => 'اسم المطوّر';

  @override
  String get loginDeveloperNameHint => 'اكتب الاسم';

  @override
  String get loginDeveloperEmail => 'إيميل المطوّر';

  @override
  String get loginDeveloperEmailHint => 'اكتب الإيميل';

  @override
  String get loginDeveloperAccess => 'الدخول لبيئة المطوّرين';

  @override
  String get profileTitle => 'البروفايل والأمان';

  @override
  String get profileNotSignedIn => 'مش مسجّل دخول';

  @override
  String get profileShowAttestation => 'عرض حالة التحقق من الجهاز';

  @override
  String get add => 'إضافة';

  @override
  String get close => 'قفل';

  @override
  String get retry => 'جرّب تاني';

  @override
  String get moreOptions => 'المزيد';

  @override
  String get chatLoadEarlier => 'تحميل الرسايل الأقدم';

  @override
  String get captureTitle => 'اسأل Copilot';

  @override
  String get captureHint => 'سجّل فكرة، أو مهمة، أو سؤال…';

  @override
  String get captureSend => 'ابعت لـ Copilot';

  @override
  String get chatToggleKnowledge => 'المعرفة';

  @override
  String get chatToggleMemory => 'الذاكرة';

  @override
  String get chatToggleTools => 'الأدوات';

  @override
  String chatComposerWaiting(String persona) {
    return 'مستني $persona...';
  }

  @override
  String chatComposerHintShort(String persona) {
    return 'اسأل $persona...';
  }

  @override
  String chatComposerHint(String persona) {
    return 'اسأل $persona أي حاجة...';
  }

  @override
  String get chatStopGenerating => 'وقّف الرد';

  @override
  String get chatSend => 'إرسال';

  @override
  String get chatOpenSessions => 'جلسات الشات';

  @override
  String get chatLiveVoice => 'صوت مباشر';

  @override
  String get chatImageLimitReached => 'وصلت للحد الأقصى للصور';

  @override
  String get chatAttachImage => 'إرفاق صورة';

  @override
  String chatAttachedImageLabel(int index, int count, String name) {
    return 'صورة مرفقة $index من $count: $name';
  }

  @override
  String chatRemoveImage(int index) {
    return 'شيل الصورة $index';
  }

  @override
  String chatSentImageLabel(int index, int count, String name) {
    return 'صورة مبعوتة $index من $count: $name. افتح المعاينة';
  }

  @override
  String get chatClosePreview => 'قفل المعاينة';

  @override
  String get chatNewSession => 'جلسة جديدة';

  @override
  String get chatSessionsHeader => 'جلسات الشات';

  @override
  String get chatDefaultSession => 'مساحة الشغل الافتراضية';

  @override
  String chatSessionLabel(String id) {
    return 'جلسة $id';
  }

  @override
  String get chatDeleteConversation => 'مسح المحادثة';

  @override
  String get chatConversationDeleted => 'المحادثة اتمسحت';

  @override
  String get chatNoReplyTitle => 'مفيش رد المرة دي';

  @override
  String get chatNoReplyBody =>
      'AURA خلّص من غير ما يرد. دوس «جرّب تاني» عشان تبعت آخر رسالة تاني.';

  @override
  String get chatEmptyTitle => 'اسأل Copilot أي حاجة';

  @override
  String get chatEmptyMessage =>
      'أسئلة برمجة، أو أسئلة دراسية، أو تنظيم مهامك.';

  @override
  String get chatSuggestExplainTitle => 'اشرحلي فكرة';

  @override
  String get chatSuggestExplainSubtitle => 'اسأل سؤال في البرمجة أو الدراسة';

  @override
  String get chatSuggestExplainPrompt =>
      'اشرحلي الـ dependency injection بيشتغل إزاي';

  @override
  String get chatSuggestHealthTitle => 'سجّل صحتك';

  @override
  String get chatSuggestHealthSubtitle =>
      'سجّل المية اللي شربتها ومدة التمرين النهارده';

  @override
  String get chatSuggestHealthPrompt => 'سجّل 500ml مية و30 دقيقة تمرين';

  @override
  String get chatSuggestFinanceTitle => 'وضعك المالي';

  @override
  String get chatSuggestFinanceSubtitle => 'اعرض رصيدي الحالي وتوزيع مصاريفي';

  @override
  String get chatSuggestFinancePrompt => 'مصاريفي عاملة إيه الشهر ده؟';

  @override
  String get chatSuggestMemoryTitle => 'ذاكرة الـ Agent';

  @override
  String get chatSuggestMemorySubtitle =>
      'شوف المعلومات اللي النظام فاكرها عني';

  @override
  String get chatSuggestMemoryPrompt => 'إنت فاكر إيه عني؟';

  @override
  String get chatRateAnswer => 'قيّم الإجابة دي';

  @override
  String chatRatingStar(int value, int max) {
    return '$value من $max';
  }

  @override
  String get chatFeedbackCorrection => 'تصحيح (اختياري)';

  @override
  String get chatFeedbackSend => 'ابعت الملاحظات';

  @override
  String get chatFeedbackSent => 'شكرًا — الملاحظات اتبعتت.';

  @override
  String get memoryBankTitle => 'بنك الذاكرة';

  @override
  String get memoryBankExplainer =>
      'دي معلومات وتفضيلات AURA عرفها عنك. بتتضاف كـ system prompts عشان التفكير يبقى مخصّص ليك.';

  @override
  String get memoryBankAddHint => 'ضيف معلومة بنفسك...';

  @override
  String get memoryBankEmpty =>
      'مفيش ذكريات متسجّلة لسه. AURA بيستخرجها لوحده من المحادثات، أو تقدر تضيف واحدة من فوق.';

  @override
  String get memoryBankDelete => 'مسح الذكرى';

  @override
  String get chatCloudUnavailableTitle =>
      'الذكاء الاصطناعي السحابي مش متاح دلوقتي';

  @override
  String get chatCloudUnavailableBody =>
      'AURA مقدرش يوصل للمساعد السحابي. تقدر تجرّب تاني أو تبلّغ عن المشكلة.';

  @override
  String get chatReportIssue => 'بلّغ عن مشكلة';

  @override
  String get pendingActionsTitle => 'AURA بيقترح إجراءات';

  @override
  String get pendingActionsReject => 'رفض';

  @override
  String get pendingActionsApprove => 'موافقة على الإجراءات';

  @override
  String get pendingActionsAllSucceeded => 'الإجراءات المقترحة اتنفذت بنجاح!';

  @override
  String pendingActionsAllFailed(String labels) {
    return 'كل الإجراءات فشلت: $labels';
  }

  @override
  String pendingActionsSomeFailed(int count, String labels) {
    return 'إجراءات فشلت ($count): $labels';
  }

  @override
  String get voiceMicPermission => 'محتاجين إذن المايك عشان نسجّل رسالة صوتية.';

  @override
  String get voiceRecordingUnavailable => 'تسجيل الصوت مش متاح على الجهاز ده.';

  @override
  String get voiceTranscribeFailed =>
      'مقدرناش نحوّل الرسالة الصوتية دي لكلام مكتوب.';

  @override
  String get voiceReadFailed => 'مقدرناش نقرا التسجيل ده.';

  @override
  String get voiceStopRecording => 'وقّف التسجيل';

  @override
  String get voiceRecordNote => 'سجّل رسالة صوتية';

  @override
  String get voiceStopPlayback => 'إيقاف';

  @override
  String get voiceListen => 'اسمع الرد ده';

  @override
  String get engineerTaskHistory => 'سجل مهام المهندس';

  @override
  String get sandboxNoChanges => '(مفيش تغييرات)';

  @override
  String get stateToneError => 'مشكلة';

  @override
  String get stateToneWarning => 'تحذير';

  @override
  String get stateToneOffline => 'مفيش اتصال';

  @override
  String get commandPaletteLabel => 'لوحة الأوامر';

  @override
  String get commandPaletteHint => 'اكتب أمر...';

  @override
  String get commandPaletteNoMatchesTitle => 'مفيش أوامر مطابقة';

  @override
  String commandPaletteNoMatchesBody(String query) {
    return 'مفيش حاجة هنا مطابقة لـ «$query». جرّب كلمة أقصر، أو امسح البحث عشان تشوف كل حاجة.';
  }

  @override
  String get commandPaletteClearSearch => 'مسح البحث';

  @override
  String get refresh => 'تحديث';

  @override
  String get changeDate => 'تغيير التاريخ';

  @override
  String get clearDueDate => 'شيل ميعاد التسليم';

  @override
  String get taskMarkDone => 'علّم إنها خلصت';

  @override
  String get taskMarkNotDone => 'علّم إنها مخلصتش';

  @override
  String get hostdBrowseExecutable => 'دوّر على ملف تشغيل الـ Daemon';

  @override
  String get hostdSaveExecutable => 'حفظ مسار ملف التشغيل';

  @override
  String get liveVoiceEnd => 'إنهاء الصوت المباشر';

  @override
  String get eventChangeStart => 'تغيير وقت البداية';

  @override
  String get eventChangeEnd => 'تغيير وقت النهاية';

  @override
  String get eventAdd => 'إضافة ميعاد';

  @override
  String get eventDelete => 'مسح الميعاد';

  @override
  String get calendarPreviousMonth => 'الشهر اللي فات';

  @override
  String get calendarNextMonth => 'الشهر الجاي';

  @override
  String get githubImportSubtask => 'استيراد كمهمة برمجة';

  @override
  String get chatActivePersonaHeader => 'الشخصية الحالية';

  @override
  String get chatUseKnowledge => 'استخدام المعرفة (RAG)';

  @override
  String get chatRoutingHeader => 'توجيه الذكاء الاصطناعي';

  @override
  String get chatAiEngine => 'محرك الذكاء الاصطناعي';

  @override
  String get chatIssueRecorded => 'المشكلة اتسجّلت في التليمتري المحلي.';

  @override
  String chatPersonaFallback(String resolved, String requested) {
    return 'الطلب ده اتعامل معاه $resolved، مش $requested -- ممكن حسابك ميكونش ليه وصول لـ $requested.';
  }

  @override
  String get chatAwaitingConfirmation => 'أكّد الإجراء اللي فوق عشان تكمّل.';

  @override
  String get personaPickerTitle => 'اختار شخصية';

  @override
  String get loginDeveloperTip =>
      'نصيحة: تسجيل الدخول بـ Google فشل. عشان تجرّب محليًا من غير ما تسجّل بصمات SHA-1 في Google Console، استخدم اختيار التخطي «الدخول لبيئة المطوّرين» اللي فوق.';

  @override
  String planIncompleteHeadline(int total, int gaps) {
    String _temp0 = intl.Intl.pluralLogic(
      gaps,
      locale: localeName,
      other: 'AURA خطط لـ $total خطوات، و$gaps خطوة منهم ماخلصتش.',
      many: 'AURA خطط لـ $total خطوات، و$gaps خطوة منهم ماخلصتش.',
      few: 'AURA خطط لـ $total خطوات، و$gaps خطوات منهم ماخلصوش.',
      two: 'AURA خطط لـ $total خطوات، وخطوتين منهم ماخلصوش.',
      one: 'AURA خطط لـ $total خطوات، وخطوة منهم ماخلصتش.',
      zero: 'AURA خطط لـ $total خطوات وكلهم خلصوا.',
    );
    return '$_temp0';
  }

  @override
  String get planIncompleteAnnouncement => 'الإجابة دي اتقالت من غيرهم.';

  @override
  String planStepWithStatus(String step, String status) {
    return '$step — $status';
  }

  @override
  String get languageSectionTitle => 'اللغة';

  @override
  String get languageSystem => 'لغة الجهاز';

  @override
  String get languageSystemSubtitle => 'استخدم نفس لغة جهازك';

  @override
  String get deviceUnverifiedBanner =>
      'مقدرناش نتأكد من الجهاز ده. ممكن شوية مميزات متشتغلش.';

  @override
  String get commonDismiss => 'إخفاء';

  @override
  String get commonSave => 'حفظ';

  @override
  String get commonDelete => 'مسح';

  @override
  String get commonEdit => 'تعديل';

  @override
  String get commonAdd => 'إضافة';

  @override
  String get commonOk => 'تمام';

  @override
  String get commonConfirm => 'تأكيد';

  @override
  String get commonUndo => 'تراجع';

  @override
  String get commonBack => 'رجوع';

  @override
  String get commonNext => 'التالي';

  @override
  String get commonDone => 'خلاص';

  @override
  String get commonCopy => 'نسخ';

  @override
  String get commonCopied => 'اتنسخ';

  @override
  String get commonRefresh => 'تحديث';

  @override
  String get commonSearch => 'بحث';

  @override
  String get commonLoading => 'جاري التحميل…';

  @override
  String get commonSomethingWentWrong => 'حصلت مشكلة';

  @override
  String get commonNone => 'مفيش';

  @override
  String get commonYes => 'أيوه';

  @override
  String get commonNo => 'لأ';
}
