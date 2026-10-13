// Tests launching the Python bridge must never inherit a user's model endpoint.
for (const key of ['GEZHI_LLM_BASE_URL', 'GEZHI_LLM_API_KEY', 'GEZHI_LLM_MODEL', 'GEZHI_LLM_MODEL_FAST',
  'GEZHI_LLM_MODEL_STRONG', 'GEZHI_REMOTE_URL', 'GEZHI_REMOTE_TOKEN']) process.env[key] = '';
