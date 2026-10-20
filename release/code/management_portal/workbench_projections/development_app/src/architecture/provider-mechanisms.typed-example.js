// Illustrative payloads only. These never determine the frozen simulation's acceptance state.
export function createTypedAnswerTeachingExample(responseCheck) {
  const context = '示例咨询：“我找不到导出按钮，能说明步骤吗？”\n示例回答：“打开报告，点击右上角的导出按钮。”';
  const questions = {
    q_route: {type: 'choice', instructions: '这条示例咨询应交给哪组？', criteria: {support: '使用问题', sales: '购买咨询'}},
    q_quality: {type: 'score', instructions: '示例回答的清晰程度？', criteria: ['不清楚', '基本清楚', '清楚']},
    q_safe: {type: 'noul', instructions: '示例回答是否给出可操作步骤？'},
  };
  const answers = {
    q_route: {type: 'choice', choice: 'support', confidence: 0.8, probabilities: {support: 0.8, sales: 0.6}},
    q_quality: {type: 'score', score: 2, confidence: 0.9},
    q_safe: {type: 'noul', noul: 0.7},
  };
  const errors = {};
  if (responseCheck === 'question_mismatch') {
    delete answers.q_quality;
    answers.q_unasked = {type: 'noul', noul: 0.6};
    errors.q_quality = '缺题：请求包含 q_quality，答案没有该 ID';
    errors.q_unasked = '多题：答案包含 q_unasked，请求没有该 ID';
  }
  if (responseCheck === 'invalid_value') {
    answers.q_route = {type: 'noul', noul: 0.8};
    answers.q_quality.score = 3;
    answers.q_safe.noul = 1.2;
    errors.q_route = '类型错误：q_route 要求 choice，答案 type 是 noul';
    errors.q_quality = '范围错误：q_quality.score = 3，三个等级只允许 0..2';
    errors.q_safe = '范围错误：q_safe.noul = 1.2，只允许 0..1';
  }
  const ids = [...new Set([...Object.keys(questions), ...Object.keys(answers)])];
  return {context, questions, answers, rows: ids.map(id => ({id, question: questions[id], answer: answers[id],
    problem: errors[id] || '', explanation: errors[id] || ({q_route: 'ID、choice 类型和标签相符；各概率在 0..1，概率和无需等于 1', q_quality: 'ID、score 类型相符；2 在 0..2 范围内', q_safe: 'ID、noul 类型相符；0.7 在 0..1 范围内'}[id])}))};
}
