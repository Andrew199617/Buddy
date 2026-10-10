export type ChatUnreadSummary = {
	count: number;
	only_chat_id: string | null;
};

export const hasUnreadChatsOutsideSelection = (
	summary: ChatUnreadSummary,
	selectedChatId: string
): boolean => {
	if (summary.count === 1) {
		return summary.only_chat_id !== selectedChatId;
	}

	return summary.count > 1;
};
