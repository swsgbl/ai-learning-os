package com.ailearningos.app.data

import com.ailearningos.app.data.remote.AdvanceSprintRequestDto
import com.ailearningos.app.data.remote.AssessmentResponseDto
import com.ailearningos.app.data.remote.AssessmentResultDto
import com.ailearningos.app.data.remote.AssessmentSubmitRequestDto
import com.ailearningos.app.data.remote.LearningContractDto
import com.ailearningos.app.data.remote.LearningSprintDto

/**
 * 学习冲刺业务面（M16-012，App/Web 同步的简版面板）。UI/ViewModel 依赖
 * 此接口；测试用 fake 实现。
 *
 * 服务端语义（不得在客户端削弱）：
 * - 阶段推进是领域层单向链（11 阶段白名单）——客户端只把用户意图发过去，
 *   409 = 非法跳段/回退，如实展示；
 * - 前后测答案隔离：assessment 下发不含答案；判分只在提交后由服务端给出；
 * - 提交幂等（重复提交 409 = 已有结果，如实展示）。
 */
interface LearningGateway {
    suspend fun contracts(): List<LearningContractDto>
    suspend fun confirmContract(contractId: String): LearningContractDto
    suspend fun contractSprints(contractId: String): List<LearningSprintDto>
    suspend fun advanceSprint(sprintId: String, toPhase: String): LearningSprintDto
    suspend fun assessment(sprintId: String, phase: String): AssessmentResponseDto
    suspend fun submitAssessment(
        sprintId: String,
        phase: String,
        responses: List<com.ailearningos.app.data.remote.AssessmentSubmitItemDto>,
    ): AssessmentResultDto
}

class LearningRepository(
    private val apiProvider: ApiProvider,
) : LearningGateway {

    override suspend fun contracts(): List<LearningContractDto> = withRemoteError {
        apiProvider.get().learningContracts()
    }

    override suspend fun confirmContract(contractId: String): LearningContractDto = withRemoteError {
        apiProvider.get().confirmLearningContract(contractId)
    }

    override suspend fun contractSprints(contractId: String): List<LearningSprintDto> = withRemoteError {
        apiProvider.get().contractSprints(contractId)
    }

    override suspend fun advanceSprint(sprintId: String, toPhase: String): LearningSprintDto =
        withRemoteError {
            apiProvider.get().advanceSprint(sprintId, AdvanceSprintRequestDto(to_phase = toPhase))
        }

    override suspend fun assessment(sprintId: String, phase: String): AssessmentResponseDto =
        withRemoteError {
            apiProvider.get().sprintAssessment(sprintId, phase)
        }

    override suspend fun submitAssessment(
        sprintId: String,
        phase: String,
        responses: List<com.ailearningos.app.data.remote.AssessmentSubmitItemDto>,
    ): AssessmentResultDto = withRemoteError {
        apiProvider.get().submitSprintAssessment(
            sprintId,
            phase,
            AssessmentSubmitRequestDto(responses = responses),
        )
    }
}
